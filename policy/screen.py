"""Rule screen and scam judge routes, mounted by the policy service (port 8001).

    POST /screen  {session_id, text, lang?, partial?}
               -> {action, hits: [{rule_id, pattern, lang, term}], refusal | null}  (contracts/screen.schema.json)
    POST /judge   {transcript, cart?, mandate_summary?, history_summary?, session_id?, mandate_id?}
               -> contracts/judge.schema.json; posts judge_scored to the relay

Policy imports `screen(text, lang, session_id=..., partial=...)` for the authoritative check in
/checkout. Standalone for testing:

    python -m uvicorn policy.screen:app --port 8011
"""

from __future__ import annotations

import asyncio
import re
import time
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from common.config import env
from policy import judge as judge_mod
from policy.rules import Verdict, evaluate, normalize

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "ai" / "prompts"
LANGS = ("en", "es", "hi")
DEFAULT_MANDATE_ID = "m_ruth_2026_09"

_DEVANAGARI = re.compile("[\u0900-\u0963\u0966-\u097F]")  # letters, not the danda
_SPANISH_MARKS = re.compile(r"[ñ¿¡áéíóú]", re.IGNORECASE)

_FUNCTION_WORDS = {
    lang: {normalize(w) for w in words.split()}
    for lang, words in {
        "en": "the and my is i to of for with need buy want please me it this he she they you",
        "es": "el la los las de que y una un mi por para con necesito compra quiero esta me lo le es",
        "hi": "hai hain mein ko ka ki ke aur mujhe mera meri kya nahi se par pe karo kharido chahiye",
    }.items()
}

# Sessions that had a final (non-partial) refusal: later final screens return at least `judge`.
_refused_sessions: set[str] = set()


def reset_sessions() -> None:
    _refused_sessions.clear()


def detect_lang(text: str, requested: str | None, verdict: Verdict | None = None) -> str:
    if requested:
        base = requested.split("-")[0].lower()
        if base in LANGS:
            return base
    if _DEVANAGARI.search(text):
        return "hi"
    tokens = normalize(text).split()
    votes = {lang: sum(t in words for t in tokens) for lang, words in _FUNCTION_WORDS.items()}
    best = max(votes, key=votes.get)
    if votes[best] and list(votes.values()).count(votes[best]) == 1:
        return best
    if verdict and verdict.hits:
        # Loanwords ("gift card", "jail") sit in several lexicons; only a match unique to a
        # language says anything about it.
        by_lang: dict[str, set[str]] = {}
        for h in verdict.hits:
            by_lang.setdefault("hi" if h.lang == "hi_latn" else h.lang, set()).add(h.matched)
        en, es, hi = (by_lang.get(k, set()) for k in ("en", "es", "hi"))
        if hi - en - es:
            return "hi"
        if es - en:
            return "es"
    if _SPANISH_MARKS.search(text):
        return "es"
    return "en"


def spoken_key_for(verdict: Verdict) -> str:
    if "R_code_reading" in verdict.rules:
        return "code_reading"
    if "R1_blocked_category" in verdict.rules:
        return "blocked_category"
    return "scam_pattern"


@lru_cache(maxsize=32)
def refusal_text(key: str, lang: str) -> str:
    path = PROMPTS / f"refusal.{key}.{lang}.txt"
    if not path.exists():
        path = PROMPTS / f"refusal.{key}.en.txt"
    return path.read_text(encoding="utf-8").strip()


def refusal_for(key: str, lang: str, rule_id: str, patterns: list[str]) -> dict:
    return {
        "rule_id": rule_id,
        "spoken_key": key,
        "patterns": patterns,
        "lang": lang,
        "text": refusal_text(key, lang),
        "audio_url": f"/audio/refusal.{key}.{lang}.mp3",
    }


def screen(text: str, lang: str | None = None, *, session_id: str | None = None,
           partial: bool = False) -> dict:
    """Return the C6 screen result for one utterance."""
    verdict = evaluate(text)
    lang_out = detect_lang(text, lang, verdict)
    action = verdict.action
    if not partial and session_id:
        if action == "refuse":
            _refused_sessions.add(session_id)
        elif session_id in _refused_sessions and action in ("slow", "proceed"):
            action = "judge"

    seen: set[tuple[str, str]] = set()
    hits = []
    for h in verdict.hits:
        if (h.rule_id, h.matched) not in seen:
            seen.add((h.rule_id, h.matched))
            hits.append({"rule_id": h.rule_id, "pattern": h.pattern, "lang": h.lang, "term": h.matched})
    refusal = None
    if action == "refuse":
        hard = next(h for h in verdict.hits if h.severity == "hard")
        key = spoken_key_for(verdict)
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
    cart: list = Field(default_factory=list)
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
