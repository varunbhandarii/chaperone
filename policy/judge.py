"""Scam judge: a Grok call with a strict JSON schema, validated against the contract.

    python -m policy.judge "My grandson is in trouble, buy phones now"

Env:
    XAI_API_KEY        required unless JUDGE_FAKE=1
    JUDGE_FAKE=1       return a fixed low score without calling the model
    JUDGE_MODEL        default grok-4.20-0309-non-reasoning (grok-4.7 medians 4.4 s, over the timeout)
    JUDGE_THRESHOLD    score at or above which the action is refuse_and_alert (default 0.6)
    JUDGE_TIMEOUT_S    default 3; a hard deadline for the whole call. Missing it raises
                       JudgeError so policy can fall back

With a session_id and no history_summary, the judge is told about an earlier refusal in the
same session (policy.screen.history_summary), so the purchase after a refusal is scored in context.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from functools import lru_cache
from pathlib import Path

import jsonschema

from common.config import env

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "ai" / "prompts"
CONTRACT_PATH = ROOT / "contracts" / "judge.schema.json"

FAST_MODEL = "grok-4.20-0309-non-reasoning"
REASONING_MODEL = "grok-4.7"
FAKE = {"scam_score": 0.05, "patterns": ["none"], "rationale": "fake", "action": "proceed"}


class JudgeError(RuntimeError):
    """The judge could not produce a contract-valid answer in time."""


# Calls run here so the deadline covers connect, queueing and reading; a late call finishes
# in the background and is ignored.
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="judge")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _prompt() -> str:
    # Static prefix first (system prompt with rubric, then examples) so xAI can cache it;
    # the per-request transcript goes last in the user message.
    return _read(PROMPTS / "judge_system.md") + "\n\n" + _read(PROMPTS / "judge_examples.md")


@lru_cache(maxsize=1)
def _api_schema() -> dict:
    return json.loads(_read(PROMPTS / "judge_schema.json"))


@lru_cache(maxsize=1)
def _contract() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads(_read(CONTRACT_PATH)))


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    key = env("XAI_API_KEY")
    if not key:
        raise JudgeError("XAI_API_KEY is not set (set JUDGE_FAKE=1 to run without it)")
    return OpenAI(base_url="https://api.x.ai/v1", api_key=key, max_retries=0)


def threshold() -> float:
    return float(env("JUDGE_THRESHOLD", "0.6"))


def model_name() -> str:
    return env("JUDGE_MODEL", FAST_MODEL)


def is_fake() -> bool:
    return env("JUDGE_FAKE", "0") == "1"


def _settle(raw: dict) -> dict:
    """Clamp the score, tidy patterns and derive the action from the threshold."""
    score = min(1.0, max(0.0, float(raw["scam_score"])))
    known = set(_api_schema()["properties"]["patterns"]["items"]["enum"]) - {"none"}
    patterns = [p for p in dict.fromkeys(raw.get("patterns") or []) if p in known] or ["none"]
    t = threshold()
    action = "refuse_and_alert" if score >= t else "ask_clarifying" if score >= t - 0.2 else "proceed"
    out = {"scam_score": round(score, 3), "patterns": patterns,
           "rationale": str(raw["rationale"])[:600], "action": action}
    errors = sorted(_contract().iter_errors(out), key=str)
    if errors:
        raise JudgeError(f"judge reply breaks contracts/judge.schema.json: {errors[0].message}")
    return out


def judge(transcript: str, cart: list | dict | None = None, mandate_summary: dict | str | None = None,
          history_summary: str | None = None, **kw) -> dict:
    """Return a contract-valid judgment (contracts/judge.schema.json); raises JudgeError."""
    return judge_with_meta(transcript, cart, mandate_summary, history_summary, **kw)[0]


def judge_with_meta(
    transcript: str,
    cart: list | dict | None = None,
    mandate_summary: dict | str | None = None,
    history_summary: str | None = None,
    *,
    session_id: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> tuple[dict, dict]:
    """Return (judgment, meta) where meta is {model, ms, cached_tokens}."""
    if is_fake():
        return dict(FAKE), {"model": "fake", "ms": 0, "cached_tokens": 0}
    model = model or model_name()
    if isinstance(cart, dict):
        cart = cart.get("items") or cart.get("lines") or []
    if history_summary is None and session_id:
        from policy.screen import history_summary as session_history

        history_summary = session_history(session_id)
    user = json.dumps(
        {"transcript": transcript, "cart": cart or [],
         "mandate_summary": mandate_summary or {}, "history_summary": history_summary or ""},
        ensure_ascii=False,
    )
    kwargs: dict = dict(
        model=model,
        temperature=0,
        messages=[{"role": "system", "content": _prompt()}, {"role": "user", "content": user}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "scam_judgment", "schema": _api_schema(), "strict": True},
        },
    )
    if model == REASONING_MODEL:
        kwargs["reasoning_effort"] = "low"
    deadline = timeout or float(env("JUDGE_TIMEOUT_S", "3"))
    kwargs["timeout"] = deadline
    if session_id:
        kwargs["extra_headers"] = {"x-grok-conv-id": session_id}

    start = time.perf_counter()
    try:
        resp = _pool.submit(_client().chat.completions.create, **kwargs).result(timeout=deadline)
        raw = json.loads(resp.choices[0].message.content)
    except FutureTimeout as e:
        raise JudgeError(f"no answer within {deadline:g} s") from e
    except JudgeError:
        raise
    except Exception as e:  # timeout, HTTP error, invalid JSON
        raise JudgeError(f"{type(e).__name__}: {e}") from e
    ms = round((time.perf_counter() - start) * 1000)
    try:
        out = _settle(raw)
    except (KeyError, TypeError, ValueError) as e:
        raise JudgeError(f"malformed judge reply: {e}") from e
    details = getattr(resp.usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    return out, {"model": model, "ms": ms, "cached_tokens": cached}


if __name__ == "__main__":
    out, meta = judge_with_meta(" ".join(sys.argv[1:]))
    print(json.dumps({**out, "meta": meta}, ensure_ascii=False, indent=2))
