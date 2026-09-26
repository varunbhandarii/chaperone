"""Rule screen and scam judge routes, mounted by the policy service (port 8001).

    POST /screen  {session_id, text, lang?, partial?}
               -> {action, hits: [{rule_id, pattern, lang, term}], refusal | null}  (contracts/screen.schema.json)
    POST /judge   {transcript, cart? (list or {items}), mandate_summary?, history_summary?, session_id?, mandate_id?}
               -> contracts/judge.schema.json; posts judge_scored to the relay

Policy imports `screen(text, lang, session_id=..., partial=...)` for the authoritative check in
/checkout, and `reset_sessions()` for POST /reset. The judge reads `history_summary(session_id)`
so a purchase after a refusal is scored with that context. Standalone for testing:

    python -m uvicorn policy.screen:app --port 8011
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from common.config import env
from policy import judge as judge_mod
from policy.rules import LANGS, Verdict, evaluate, guess_lang

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "ai" / "prompts"
DEFAULT_MANDATE_ID = "m_ruth_2026_09"

# Sessions with a final (non-partial) refusal: later final screens return at least `judge`,
# and the judge is told about the refusal. Bounded by size and age.
MAX_SESSIONS = 1000
SESSION_TTL_S = 6 * 3600
_refused: OrderedDict[str, dict] = OrderedDict()

_HISTORY = {
    "blocked_category": "a request for {what} was refused earlier in this session",
    "code_reading": "a request to read out card numbers or codes was refused earlier in this session",
    "scam_pattern": "a request that looked coached by a scammer was refused earlier in this session",
}


def reset_sessions() -> None:
    _refused.clear()


def _remember(session_id: str, spoken_key: str, terms: list[str]) -> None:
    _refused[session_id] = {"spoken_key": spoken_key, "terms": terms, "t": time.time()}
    _refused.move_to_end(session_id)
    while len(_refused) > MAX_SESSIONS:
        _refused.popitem(last=False)


def _recall(session_id: str | None) -> dict | None:
    entry = _refused.get(session_id) if session_id else None
    if entry and time.time() - entry["t"] > SESSION_TTL_S:
        del _refused[session_id]
        return None
    return entry


def history_summary(session_id: str | None) -> str:
    """Plain-English note of an earlier refusal in this session, or "" if there was none."""
    entry = _recall(session_id)
    if not entry:
        return ""
    what = ", ".join(entry["terms"][:2]) or "a blocked item"
    return _HISTORY.get(entry["spoken_key"], _HISTORY["scam_pattern"]).format(what=what)


def detect_lang(text: str, requested: str | None, verdict: Verdict | None = None) -> str:
    guess = guess_lang(text, requested)
    if guess:
        return guess
    if verdict and verdict.hits:
        # Loanwords ("gift card", "jail") sit in several lexicons; only a match unique to a
        # language says anything about it.
        by_lang: dict[str, set[str]] = {}
        for h in verdict.hits:
            by_lang.setdefault("hi" if h.lang == "hi_latn" else h.lang, set()).add(h.matched.casefold())
        en, es, hi = (by_lang.get(k, set()) for k in ("en", "es", "hi"))
        if hi - en - es:
            return "hi"
        if es - en:
            return "es"
    return "en"


def spoken_key_for(verdict: Verdict) -> str:
    if "R_code_reading" in verdict.rules:
        return "code_reading"
    if "R1_blocked_category" in verdict.rules:
        return "blocked_category"
    return "scam_pattern"


@lru_cache(maxsize=len(LANGS) + 1)
def lines(lang: str) -> dict[str, str]:
    """Spoken lines for one language (ai/prompts/lines.<lang>.json), English as the fallback."""
    lang = lang if lang in LANGS else "en"
    return json.loads((PROMPTS / f"lines.{lang}.json").read_text(encoding="utf-8"))


def refusal_for(key: str, lang: str, rule_id: str, patterns: list[str]) -> dict:
    return {
        "rule_id": rule_id,
        "spoken_key": key,
        "patterns": patterns,
        "lang": lang,
        "text": lines(lang)[key],
        "audio_url": f"/audio/refusal.{key}.{lang}.mp3",
    }


def screen(text: str, lang: str | None = None, *, session_id: str | None = None,
           partial: bool = False) -> dict:
    """Return the C6 screen result for one utterance."""
    verdict = evaluate(text, lang)
    lang_out = detect_lang(text, lang, verdict)
    action = verdict.action
    key = spoken_key_for(verdict)
    if not partial and session_id:
        if action == "refuse":
            blocked = [h.matched for h in verdict.hits if h.rule_id == "R1_blocked_category"]
            _remember(session_id, key, list(dict.fromkeys(blocked)))
        elif _recall(session_id) and action in ("slow", "proceed"):
            action = "judge"

    seen: set[tuple[str, str]] = set()
    hits = []
    for h in verdict.hits:
        if (h.rule_id, h.matched.casefold()) not in seen:
            seen.add((h.rule_id, h.matched.casefold()))
            hits.append({"rule_id": h.rule_id, "pattern": h.pattern, "lang": h.lang, "term": h.matched})
    refusal = None
    if action == "refuse":
        hard = next(h for h in verdict.hits if h.severity == "hard")
        refusal = refusal_for(key, lang_out, hard.rule_id, verdict.patterns)
    return {"action": action, "hits": hits, "refusal": refusal}


# ---------------------------------------------------------------- relay events

async def post_event(type_: str, session_id: str | None, mandate_id: str | None, **fields) -> None:
    relay = env("RELAY_URL")
    if not relay:
        return
    event = {"type": type_, "session_id": session_id or "none", "mandate_id": mandate_id or DEFAULT_MANDATE_ID,
             "t": int(time.time() * 1000), "source": "policy", **fields}
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            await client.post(f"{relay.rstrip('/')}/events", json=event)
    except httpx.HTTPError as e:
        print(f"[screen] relay event {type_} failed: {e}", flush=True)


# ---------------------------------------------------------------- routes

class ScreenBody(BaseModel):
    session_id: str
    text: str
    lang: str | None = None
    partial: bool = False


class JudgeBody(BaseModel):
    transcript: str
    cart: list | dict | None = None
    mandate_summary: dict | str | None = None
    history_summary: str | None = None
    session_id: str | None = None
    mandate_id: str | None = None


router = APIRouter()


@router.post("/screen")
def screen_route(body: ScreenBody) -> dict:
    return screen(body.text, body.lang, session_id=body.session_id, partial=body.partial)


@router.post("/judge")
async def judge_route(body: JudgeBody, tasks: BackgroundTasks) -> dict:
    try:
        out, meta = await asyncio.to_thread(
            judge_mod.judge_with_meta, body.transcript, body.cart, body.mandate_summary,
            body.history_summary, session_id=body.session_id,
        )
    except judge_mod.JudgeError as e:
        raise HTTPException(status_code=503, detail=f"judge unavailable: {e}") from e
    tasks.add_task(post_event, "judge_scored", body.session_id, body.mandate_id,
                   scam_score=out["scam_score"], patterns=out["patterns"], action=out["action"],
                   model=meta["model"], ms=meta["ms"], cached_tokens=meta["cached_tokens"])
    return out


app = FastAPI(title="Chaperone screen (standalone)")
app.include_router(router)
