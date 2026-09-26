"""Rule screen and scam judge routes, mounted by the policy service (port 8001).

    POST /screen  {session_id, text, lang?, partial?}
               -> {action, hits: [{rule_id, pattern, lang, term}], refusal | null}  (contracts/screen.schema.json)
               A hard hit inside a story about someone else ("Peachtree Power called and said...") answers
               action "scam_check" instead of "refuse": the station then calls POST /scam-check, which sets
               the cool-down and alerts Priyank. The refusal is still attached as the fallback if that fails.
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
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from common import tls
from common.config import env
from policy import judge as judge_mod
from policy.rules import LANGS, Verdict, default_rules, evaluate, guess_lang

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
    "refund_scam": "a refund or recovery scam request was refused earlier in this session",
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
        _refused.pop(session_id, None)
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


SPOKEN_KEY_ORDER = ("refund_scam", "code_reading", "blocked_category")
_RULE_KEYS = {"R_code_reading": "code_reading", "R1_blocked_category": "blocked_category"}


def spoken_key_for(verdict: Verdict) -> str:
    """The refusal line: a rule's own spoken_key, most specific first."""
    rules = default_rules().rules
    keys = {rules[r].get("spoken_key") or _RULE_KEYS.get(r) for r in verdict.rules}
    return next((k for k in SPOKEN_KEY_ORDER if k in keys), "scam_pattern")


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
    return _screen(text, lang, session_id=session_id, partial=partial)[0]


def _screen(text: str, lang: str | None, *, session_id: str | None, partial: bool) -> tuple[dict, Verdict]:
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
        rules = default_rules().rules
        hard_hits = [h for h in verdict.hits if h.severity == "hard"]
        # the rule named on the wall and in "Why?" is the one whose line Ruth hears
        hard = next((h for h in hard_hits
                     if ((rules.get(h.rule_id) or {}).get("spoken_key") or _RULE_KEYS.get(h.rule_id)) == key),
                    hard_hits[0])
        refusal = refusal_for(key, lang_out, hard.rule_id, verdict.patterns)
    return {"action": action, "hits": hits, "refusal": refusal}, verdict


# ---------------------------------------------------------------- relay events

async def post_event(type_: str, session_id: str | None, mandate_id: str | None, **fields) -> None:
    relay = env("RELAY_URL")
    if not relay:
        return
    event = {"type": type_, "session_id": session_id or "none", "mandate_id": mandate_id or DEFAULT_MANDATE_ID,
             "t": int(time.time() * 1000), "source": "policy", **fields}
    try:
        async with httpx.AsyncClient(verify=tls.context(), timeout=0.5) as client:
            await client.post(f"{relay.rstrip('/')}/events", json=event)
    except httpx.HTTPError as e:
        print(f"[screen] relay event {type_} failed: {e}", flush=True)


# ---------------------------------------------------------------- routes

class ScreenBody(BaseModel):
    session_id: str
    text: str
    lang: str | None = None
    partial: bool = False
    mandate_id: str | None = None


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
    out, verdict = _screen(body.text, body.lang, session_id=body.session_id, partial=body.partial)
    if out["action"] == "refuse" and verdict.story:
        # Ruth is telling us about a call, not asking to buy: the scam check answers, cools the card
        # and alerts Priyank (it records its own check, so no decision or alert here).
        out["action"] = "scam_check"
        return out
    if not body.partial:
        report(out, body.text, body.session_id, body.mandate_id or DEFAULT_MANDATE_ID)
    return out


def report(out: dict, text: str, session_id: str, mandate_id: str) -> None:
    """Make a final screen visible: a caution row for soft signals; for a refusal, a stored decision
    and an alert to Priyank carrying its decision_id, so his "Why?" can explain it."""
    from policy.events import post_event as post

    rule_ids = sorted({h["rule_id"] for h in out["hits"]})
    if out["action"] in ("slow", "judge"):
        if not rule_ids:  # "judge" only because the session had a refusal earlier: nothing to show
            return
        post("caution", session_id, mandate_id, rule_ids=rule_ids, action=out["action"],
             words=[h["term"] for h in out["hits"]][:6])
        return
    if out["action"] != "refuse":
        return
    from policy.store import save_decision

    refusal = out["refusal"]
    decision_id = "d_" + uuid.uuid4().hex[:12]
    save_decision({
        "decision_id": decision_id, "decision": "deny", "source": "screen", "say_key": refusal["spoken_key"],
        "rules": [{"id": f"S_screen_{rule_id}", "passed": False, "detail": refusal["spoken_key"]} for rule_id in rule_ids],
        "judge": None, "cart": {"items": [], "total": 0}, "order": None, "approval": None,
        "mandate_id": mandate_id, "session_id": session_id, "lang": refusal["lang"],
        "ruth_said": text[:400], "screen_hits": out["hits"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    refusal["decision_id"] = decision_id
    post("caregiver_alerted", session_id, mandate_id, kind="screen_refusal", decision_id=decision_id,
         rule_ids=rule_ids, spoken_key=refusal["spoken_key"], patterns=refusal["patterns"], lang=refusal["lang"])


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
