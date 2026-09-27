"""Plain-English explanation of one decision for Priyank ("Why did it refuse?").

    from ai.explain import explain
    explain(decision_doc, ruth_said="...", screen_hits=[...])
    -> {headline, what_happened, rule_in_plain_words, what_ruth_heard, what_you_can_do}

`decision_doc` is the stored policy decision ({decision, rules, say_key, judge, cart, ...}).
Policy's GET /decisions/{id}/explain calls this once per decision and caches the answer.

Env:
    XAI_API_KEY         required unless EXPLAIN_FAKE=1
    EXPLAIN_FAKE=1      a fixed answer, for tests
    EXPLAIN_MODEL       default grok-4.20-0309-non-reasoning
    EXPLAIN_TIMEOUT_S   default 3; a hard deadline for the whole call (raises ExplainError)
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from functools import lru_cache
from pathlib import Path

from common.config import env

PROMPTS = Path(__file__).resolve().parent / "prompts"
FIELDS = ("headline", "what_happened", "rule_in_plain_words", "what_ruth_heard", "what_you_can_do")
MODEL = "grok-4.20-0309-non-reasoning"
FAKE = {
    "headline": "Chaperone stopped a purchase",
    "what_happened": "Ruth asked for something the spending rules do not allow, so nothing was bought.",
    "rule_in_plain_words": "The safety rules you signed block this kind of purchase.",
    "what_ruth_heard": "Chaperone told Ruth kindly that it could not buy it and that you would call.",
    "what_you_can_do": "Give Ruth a call to check in.",
}
# Things Priyank should never see: rule ids, field names, scores.
_LEAKS = re.compile(r"\b(?:R\d\w*|RF\d\w*|R_[a-z_]+|S_screen\w*|scam_score|say_key|judge|0\.\d+)\b")
_ALARM = re.compile(r"!|\b(?:urgent|danger(?:ous)?|attack|alarming|emergency)\b", re.IGNORECASE)
_JARGON = re.compile(r"\b(?:impersonation|amount anomaly|third[- ]party instruction|blocked category|family emergency|"
                     r"code reading|refund rail)\b", re.IGNORECASE)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="explain")


class ExplainError(RuntimeError):
    """No valid explanation within the deadline."""


@lru_cache(maxsize=1)
def _system() -> str:
    return (PROMPTS / "explain_system.md").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads((PROMPTS / "explain_schema.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _spoken_en() -> dict:
    return json.loads((PROMPTS / "lines.en.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    key = env("XAI_API_KEY")
    if not key:
        raise ExplainError("XAI_API_KEY is not set (set EXPLAIN_FAKE=1 to run without it)")
    return OpenAI(base_url="https://api.x.ai/v1", api_key=key, max_retries=0)


def build_input(decision: dict, *, ruth_said: str = "", screen_hits: list | None = None,
                limits: dict | None = None) -> dict:
    """The model's input: only what the explanation may use."""
    judge = decision.get("judge") or None
    cart = decision.get("cart") or {}
    return {
        "decision": decision.get("decision"),
        "rules_failed": [{"id": r.get("id"), "detail": r.get("detail", "")}
                         for r in decision.get("rules") or [] if not r.get("passed", True)],
        "screen_hits": [{"pattern": h.get("pattern"), "words": h.get("term")} for h in screen_hits or []],
        "judge": {k: judge.get(k) for k in ("scam_score", "patterns", "rationale")} if judge else None,
        "ruth_said": ruth_said[:400],
        "ruth_heard": _spoken_en().get(decision.get("say_key") or "", ""),
        "cart": {"items": [{"name": i.get("name"), "qty": i.get("qty"), "price": i.get("price")}
                           for i in cart.get("items") or []],
                 "total": cart.get("total")},
        "limits": limits or {},
        "scam_check": _scam_check(decision.get("scam_check")),
    }


def _scam_check(check: dict | None) -> dict | None:
    """What the scam radar found, for a decision that came from POST /scam-check."""
    if not check:
        return None
    return {"verdict": check.get("verdict"), "pattern": check.get("pattern"),
            "facts_checked": check.get("facts_checked") or [], "reported_recently": check.get("reported_recently"),
            "amount": check.get("amount"), "card_cooldown_until": check.get("cooldown_until"),
            "sources": [s.get("title") or s.get("url") for s in check.get("sources") or []][:5]}


def problems(out: dict) -> list[str]:
    """Tone and format problems in an explanation (empty when it is fine)."""
    found = []
    for field in FIELDS:
        text = str(out.get(field) or "").strip()
        if not text:
            found.append(f"{field}: empty")
            continue
        if _LEAKS.search(text):
            found.append(f"{field}: shows an id, score or field name")
        if _ALARM.search(text):
            found.append(f"{field}: alarming wording")
        if _JARGON.search(text):
            found.append(f"{field}: jargon")
        if len(re.findall(r"[.?।](?:\s|$)", text)) > 1:
            found.append(f"{field}: more than one sentence")
        if len(text.split()) > 32:
            found.append(f"{field}: over 30 words")
    return found


def explain(decision: dict, *, ruth_said: str = "", screen_hits: list | None = None,
            limits: dict | None = None, timeout: float | None = None) -> dict:
    """Return the five fields; raises ExplainError on a timeout or a malformed answer."""
    if env("EXPLAIN_FAKE", "0") == "1":
        return dict(FAKE)
    deadline = timeout or float(env("EXPLAIN_TIMEOUT_S", "3"))
    user = json.dumps(build_input(decision, ruth_said=ruth_said, screen_hits=screen_hits, limits=limits),
                      ensure_ascii=False)
    kwargs = dict(
        model=env("EXPLAIN_MODEL", MODEL),
        temperature=0,
        timeout=deadline,
        messages=[{"role": "system", "content": _system()}, {"role": "user", "content": user}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "decision_explanation", "schema": _schema(), "strict": True}},
    )
    try:
        resp = _pool.submit(_client().chat.completions.create, **kwargs).result(timeout=deadline)
        raw = json.loads(resp.choices[0].message.content)
    except FutureTimeout as e:
        raise ExplainError(f"no answer within {deadline:g} s") from e
    except ExplainError:
        raise
    except Exception as e:  # HTTP error, invalid JSON
        raise ExplainError(f"{type(e).__name__}: {e}") from e
    if not isinstance(raw, dict) or any(not isinstance(raw.get(f), str) for f in FIELDS):
        raise ExplainError("malformed explanation")
    return {f: raw[f].strip() for f in FIELDS}


if __name__ == "__main__":
    import sys

    start = time.perf_counter()
    doc = json.loads(sys.stdin.read())
    print(json.dumps(explain(doc), ensure_ascii=False, indent=2), f"\n{(time.perf_counter() - start) * 1000:.0f} ms")
