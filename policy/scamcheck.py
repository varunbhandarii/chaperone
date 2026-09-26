"""The Ask guard's scam radar: POST /scam-check.

    POST /scam-check {session_id, mandate_id, lang, channel: station|line, story?, transcript?,
                      caller: {org, name, phone}}      (story or transcript, or both)
      -> {check_id, verdict: scam|unsure|ok, pattern, say, actions[], facts_checked[], sources[],
          cooldown_until, ms, from_cache}
    GET  /scam-check/{check_id}     the stored check, for Priyank's app

`story` is the voice model's summary; `transcript` is Ruth's exact words. The rules screen both, so a
paraphrase can't hide a hard hit, and Grok sees both.

Order of work:
1. The rule screen. A hard hit answers at once (the say line from the cache or lines.<lang>.json);
   Grok then runs in the background to attach sources to Priyank's alert.
2. Facts from Ruth's own accounts: trusted contacts (mandate v2), the biller balance and
   recent orders. They go to Grok and come back as facts_checked.
3. Grok Responses with x_search and web_search, within RADAR_TIMEOUT_S (12 s); then the cache
   (sessions/radar_cache.json, by story and by pattern and language); then a rules-only verdict.

Events: scam_checked; for a scam, caregiver_alerted {check_id, sources} and risk_changed (a 24-hour
card cool-down through policy.risk).

Env: XAI_API_KEY, RADAR_MODEL (grok-4.20-0309-non-reasoning), RADAR_TIMEOUT_S (12), RADAR_FAKE=1
(no network: rules, cache and fallback only), MERCHANT_PUBLIC_URL for the biller.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.config import decisions_path, env, merchant_public_url
from policy import risk
from policy.events import post_event
from policy.rules import normalize
from policy.screen import lines, screen

RESPONSES_URL = "https://api.x.ai/v1/responses"
DOMAINS = ["consumer.ftc.gov", "ic3.gov", "aarp.org", "bbb.org", "fcc.gov"]
DEFAULT_MANDATE_ID = "m_ruth_2026_09"
COOLDOWN_HOURS = 24
LANG_NAMES = {"en": "English", "es": "Spanish", "hi": "Hindi"}

# Until mandate v2 and the biller land on main, the demo account's facts.
DEMO_TRUSTED = [{"name": "Priyank", "relation": "son", "phone": "+1-404-555-0142"},
                {"name": "Alex", "relation": "grandson", "phone": "+1-404-555-0187"}]
DEMO_BILLERS = [{"merchant_id": "peachtree_power", "account_ref": "PP-2231-0098"}]
BILLER_WORDS = re.compile(r"\b(?:power|electric\w*|utility|light bill|peachtree|luz|electricidad|bijli|बिजली|लाइट)\b",
                          re.IGNORECASE)
ORDER_WORDS = re.compile(r"\b(?:order\w*|refund\w*|overcharg\w*|amazon|pedido|reembols\w*|ऑर्डर|रिफंड)\b", re.IGNORECASE)

# Rule pattern -> radar pattern, most specific first.
RULE_PATTERNS = [
    ("refund_overpay", "refund_overpayment"), ("refund_fee", "refund_fee"), ("recovery_fee", "recovery_scam"),
    ("remote_access", "tech_support"), ("refund_rail", "refund_overpayment"), ("customs_hold", "parcel_customs"),
    ("redelivery_fee", "fake_delivery"), ("parcel_illegal", "digital_arrest"), ("renewal_callback", "fake_renewal"),
    ("silence_request", "bank_impersonation"), ("utility_shutoff", "utility_shutoff"),
    ("safe_account", "safe_account"), ("crypto_atm", "crypto_atm"), ("courier_pickup", "courier_pickup"),
    ("family_emergency", "grandparent_emergency"), ("code_reading", "gift_card_codes"),
    ("authority_impersonation", "government_impersonation"), ("blocked_category", "gift_card_demand"),
]

INSTRUCTIONS = (
    "You protect Ruth, an older adult, from scams. You get her words, what the caller claimed, facts from her own "
    "accounts, and hints from safety rules. Search X (last 30 days) and the web for current reports of this scam. "
    "Reply only in the schema.\n"
    "- verdict: scam when the story matches a known scam or contradicts her facts; unsure when you cannot tell; "
    "ok for an ordinary request.\n"
    "- pattern: a short snake_case name for the scam (for example grandparent_emergency, utility_shutoff, "
    "tech_support, safe_account, crypto_atm), or none.\n"
    "- say: in the language given, speaking to Ruth formally (usted in Spanish, aap in Hindi), exactly two short "
    "sentences. The first says plainly what this looks like, using a fact from her accounts when there is one. The "
    "second gives exactly one action: hang up, do not pay, or call a trusted person at the number she has saved. "
    "Warm, calm, never a score, never blame her, no source names.\n"
    "- actions: the one or two actions that fit.\n"
    "- reported_recently: one short English sentence on what the sources report now, or 'no'.\n"
    "Only use sources the tools returned."
)
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "pattern", "say", "actions", "reported_recently"],
    "properties": {
        "verdict": {"type": "string", "enum": ["scam", "unsure", "ok"]},
        "pattern": {"type": "string"},
        "say": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "string", "enum": [
            "hang_up", "call_priya", "call_trusted", "do_not_pay", "none"]}},
        "reported_recently": {"type": "string"},
    },
}

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="radar")
_lock = threading.RLock()


class RadarError(RuntimeError):
    """Grok gave no usable verdict in time."""


# ---------------------------------------------------------------- storage

def _path(name: str, var: str) -> Path:
    return Path(env(var, str(decisions_path().parent / name)))


def cache_path() -> Path:
    return _path("radar_cache.json", "RADAR_CACHE_PATH")


def checks_path() -> Path:
    return _path("scam_checks.json", "SCAM_CHECKS_PATH")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def story_key(story: str, lang: str) -> str:
    return f"story:{lang}:{hashlib.sha1(normalize(story).encode()).hexdigest()[:16]}"


def cache_get(*keys: str) -> dict | None:
    with _lock:
        data = _read(cache_path())
    return next((data[k] for k in keys if k in data), None)


def cache_put(entry: dict, *keys: str) -> None:
    with _lock:
        data = _read(cache_path())
        for k in keys:
            data[k] = entry
        _write(cache_path(), data)


def save_check(doc: dict) -> None:
    with _lock:
        data = _read(checks_path())
        data[doc["check_id"]] = doc
        _write(checks_path(), data)


def get_check(check_id: str) -> dict | None:
    with _lock:
        return _read(checks_path()).get(check_id)


def reset() -> None:
    """Forget stored checks (the radar cache survives: it holds the warmed demo answers)."""
    with _lock:
        if checks_path().exists():
            checks_path().unlink()


# ---------------------------------------------------------------- facts from Ruth's accounts

def _mandate() -> dict:
    try:
        from policy.store import load_mandate

        return load_mandate() or {}
    except Exception:  # noqa: BLE001 - facts are best effort
        return {}


def _biller_account(merchant_id: str, account_ref: str) -> dict | None:
    try:
        r = httpx.get(f"{merchant_public_url()}/billers/{merchant_id}/accounts/{account_ref}", timeout=1.0)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def _recent_orders(mandate_id: str) -> list[dict]:
    try:
        from policy.store import load_decisions
    except ImportError:
        return []
    out = []
    for doc in load_decisions().values():
        if doc.get("mandate_id") == mandate_id and doc.get("order"):
            cart = doc.get("cart") or {}
            out.append({"at": doc.get("created_at") or "", "total": cart.get("total"),
                        "store": cart.get("merchant") or "corner_market"})
    return sorted(out, key=lambda o: o["at"], reverse=True)[:5]


def gather_facts(mandate_id: str, story: str, caller: dict) -> list[dict]:
    """Facts from Ruth's own accounts that bear on the story: [{fact, result}]."""
    mandate = _mandate()
    facts: list[dict] = []
    text = f"{story} {caller.get('name') or ''} {caller.get('org') or ''}"
    for contact in mandate.get("trusted_contacts") or DEMO_TRUSTED:
        name = contact.get("name") or ""
        if name and re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            result = contact.get("phone") or "on file"
            if caller.get("phone") and contact.get("phone"):
                same = re.sub(r"\D", "", caller["phone"])[-10:] == re.sub(r"\D", "", contact["phone"])[-10:]
                result += ", the same as the caller" if same else ", different from the caller"
            facts.append({"fact": f"{name}'s number on file ({contact.get('relation', 'contact')})", "result": result})
    if BILLER_WORDS.search(text):
        for biller in mandate.get("billers") or DEMO_BILLERS:
            account = _biller_account(biller["merchant_id"], biller["account_ref"])
            if account:
                due = account.get("balance_due")
                bits = [f"${due} due {account.get('due_date')}" if due and float(due) > 0 else "nothing due",
                        "past due" if account.get("past_due") else "not past due",
                        "a disconnect notice is on file" if account.get("disconnect_notice") else "no disconnect notice"]
                facts.append({"fact": f"{account.get('biller', biller['merchant_id'])} account", "result": ", ".join(bits)})
            else:
                facts.append({"fact": f"{biller['merchant_id']} account", "result": "could not be checked just now"})
    if ORDER_WORDS.search(text):
        orders = _recent_orders(mandate_id)
        facts.append({"fact": "Ruth's recent orders", "result": (
            f"{len(orders)} in the last 30 days, the latest ${orders[0]['total']} at {orders[0]['store']}"
            if orders else "no orders in the last 30 days")})
    return facts


# ---------------------------------------------------------------- Grok radar

def radar(story: str, lang: str, caller: dict, facts: list[dict], hints: list[str], timeout: float,
          transcript: str = "") -> dict:
    """One Grok Responses call with X and web search; raises RadarError."""
    if env("RADAR_FAKE", "0") == "1":
        raise RadarError("RADAR_FAKE=1")
    key = env("XAI_API_KEY")
    if not key:
        raise RadarError("XAI_API_KEY is not set")
    today = dt.date.today()
    said = f"Ruth's exact words: {transcript}\nSummary: {story}" if transcript else f"Ruth said: {story}"
    content = (f"Language: {LANG_NAMES.get(lang, 'English')}. {said}\n"
               f"Caller claimed: {json.dumps(caller, ensure_ascii=False)}\n"
               f"Facts from her accounts: {json.dumps(facts, ensure_ascii=False)}\n"
               f"Safety rule hints: {', '.join(hints) or 'none'}")
    body = {
        "model": env("RADAR_MODEL", "grok-4.20-0309-non-reasoning"),
        "instructions": INSTRUCTIONS,
        "input": [{"role": "user", "content": content}],
        "tools": [{"type": "x_search", "from_date": (today - dt.timedelta(days=30)).isoformat(), "to_date": today.isoformat()},
                  {"type": "web_search", "filters": {"allowed_domains": DOMAINS}}],
        "max_turns": 3,
        "include": ["no_inline_citations"],
        "text": {"format": {"type": "json_schema", "name": "scam_verdict", "strict": True, "schema": SCHEMA}},
    }

    def call() -> dict:
        r = httpx.post(RESPONSES_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

    try:
        resp = _pool.submit(call).result(timeout=timeout)
    except FutureTimeout as e:
        raise RadarError(f"no answer within {timeout:g} s") from e
    except Exception as e:  # noqa: BLE001 - HTTP errors, bad JSON
        raise RadarError(f"{type(e).__name__}: {e}") from e
    verdict, sources = None, []
    parts = [part for item in resp.get("output", []) if item.get("type") == "message"
             for part in item.get("content") or [] if part.get("type") == "output_text"]
    for part in parts:
        try:
            verdict = json.loads(part.get("text") or "")
        except json.JSONDecodeError as e:
            raise RadarError("unparsable verdict") from e
        sources += [{"title": a.get("title") or "", "url": a["url"]}
                    for a in part.get("annotations") or [] if a.get("type") == "url_citation" and a.get("url")]
    if not isinstance(verdict, dict) or verdict.get("verdict") not in ("scam", "unsure", "ok") or not verdict.get("say"):
        raise RadarError("no usable verdict")
    verdict["sources"] = list({s["url"]: s for s in sources}.values())[:8]
    verdict["cost_usd_ticks"] = (resp.get("usage") or {}).get("cost_in_usd_ticks")
    return verdict


# ---------------------------------------------------------------- the check

def _rule_pattern(patterns: list[str]) -> str | None:
    return next((radar_p for rule_p, radar_p in RULE_PATTERNS if rule_p in patterns), None)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "unknown"


def _actions(raw: list[str], verdict: str, facts: list[dict]) -> list[str]:
    trusted = next((f["fact"].split("'s number")[0] for f in facts if "'s number on file" in f["fact"]
                    and not f["fact"].startswith("Priya")), None)
    out = []
    for a in raw or []:
        if a == "call_trusted" and trusted:
            out.append(f"call_trusted:{trusted}")
        elif a != "none":
            out.append(a)
    if verdict == "scam":
        out = (out or ["hang_up"]) + ["tell_priya"]
    return list(dict.fromkeys(out))


def _fallback(verdict: str, lang: str) -> str:
    return lines(lang)[f"scam_check_{verdict}"]


def _enrich_in_background(doc: dict, story: str, caller: dict, facts: list[dict], hints: list[str],
                          transcript: str = "") -> None:
    """After a hard-rule answer, fetch sources for Priyank's alert and warm the cache."""
    def run() -> None:
        try:
            v = radar(story, doc["lang"], caller, facts, hints, float(env("RADAR_TIMEOUT_S", "12")), transcript)
        except RadarError:
            v = None
        if v and v.get("verdict") == "scam":
            entry = {k: v[k] for k in ("verdict", "pattern", "say", "actions", "reported_recently", "sources")}
            cache_put(entry, story_key(transcript or story, doc["lang"]), f"pattern:{doc['pattern']}:{doc['lang']}")
            doc["sources"] = v["sources"]
            doc["reported_recently"] = v.get("reported_recently")
            save_check(doc)
        _alert(doc)

    threading.Thread(target=run, daemon=True).start()


def _alert(doc: dict) -> None:
    post_event("caregiver_alerted", doc["session_id"], doc["mandate_id"], kind="scam_check", check_id=doc["check_id"],
               pattern=doc["pattern"], say=doc["say"], sources=doc["sources"], cooldown_until=doc["cooldown_until"])


def check(story: str = "", lang: str = "en", *, session_id: str | None = None, mandate_id: str | None = None,
          channel: str = "station", caller: dict | None = None, transcript: str = "") -> dict:
    start = time.perf_counter()
    story, transcript = (story or "").strip(), (transcript or "").strip()
    story = story or transcript
    heard = " ".join(dict.fromkeys(t for t in (transcript, story) if t))  # what the rules screen
    key_text = transcript or story
    lang = lang if lang in LANG_NAMES else "en"
    mandate_id = mandate_id or DEFAULT_MANDATE_ID
    session_id = session_id or "none"
    caller = {k: (caller or {}).get(k) for k in ("org", "name", "phone")}
    check_id = "sc_" + uuid.uuid4().hex[:10]

    screened = screen(heard, lang, session_id=session_id if session_id != "none" else None)
    patterns = [h["pattern"] for h in screened["hits"]]
    hints = sorted(set(patterns))
    hard = screened["action"] == "refuse"
    facts = gather_facts(mandate_id, heard, caller)
    rule_pattern = _rule_pattern(patterns)
    from_cache, v = False, None

    if hard:
        pattern = rule_pattern or "gift_card_demand"
        cached = cache_get(story_key(key_text, lang), f"pattern:{pattern}:{lang}")
        verdict, from_cache = "scam", bool(cached)
        say = (cached or {}).get("say") or _fallback("scam", lang)
        raw_actions = (cached or {}).get("actions") or ["hang_up", "do_not_pay"]
        sources = (cached or {}).get("sources") or []
        reported = (cached or {}).get("reported_recently")
    else:
        try:
            v = radar(story, lang, caller, facts, hints, float(env("RADAR_TIMEOUT_S", "12")), transcript)
        except RadarError:
            keys = [story_key(key_text, lang)] + ([f"pattern:{rule_pattern}:{lang}"] if rule_pattern else [])
            v = cache_get(*keys)
            from_cache = bool(v)
        if v:
            verdict, say, raw_actions = v["verdict"], v["say"], v.get("actions") or []
            sources, reported = v.get("sources") or [], v.get("reported_recently")
            grok_pattern = _slug(v.get("pattern"))
            pattern = grok_pattern if grok_pattern not in ("none", "unknown") else (rule_pattern or grok_pattern)
            if not from_cache and verdict == "scam":
                entry = {k: v.get(k) for k in ("verdict", "pattern", "say", "actions", "reported_recently", "sources")}
                cache_put(entry, story_key(key_text, lang), f"pattern:{pattern}:{lang}")
        else:
            # Rules only: two or more soft signals are treated as a scam, one as unsure.
            verdict = "scam" if screened["action"] == "judge" else "unsure"
            pattern = rule_pattern or "unknown"
            say, raw_actions = _fallback(verdict, lang), ["do_not_pay"]
            sources, reported = [], None

    cooldown_until = None
    if verdict == "scam":
        cooldown_until = risk.set_cooldown(mandate_id, COOLDOWN_HOURS, pattern, check_id, session_id)["cooldown_until"]
    doc = {
        "check_id": check_id, "verdict": verdict, "pattern": pattern, "say": say,
        "actions": _actions(raw_actions, verdict, facts), "facts_checked": facts, "sources": sources,
        "reported_recently": reported, "cooldown_until": cooldown_until,
        "ms": round((time.perf_counter() - start) * 1000), "from_cache": from_cache,
        "session_id": session_id, "mandate_id": mandate_id, "lang": lang, "channel": channel,
        "story": story[:400], "transcript": transcript[:1000], "rule_ids": sorted({h["rule_id"] for h in screened["hits"]}),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    save_check(doc)
    post_event("scam_checked", session_id, mandate_id, check_id=check_id, verdict=verdict, pattern=pattern,
               sources=sources, ms=doc["ms"], channel=channel)
    if verdict == "scam":
        if hard and not sources:
            _enrich_in_background(doc, story, caller, facts, hints, transcript)
        else:
            _alert(doc)
    return public(doc)


def public(doc: dict) -> dict:
    keys = ("check_id", "verdict", "pattern", "say", "actions", "facts_checked", "sources", "cooldown_until", "ms",
            "from_cache")
    return {k: doc.get(k) for k in keys}


# ---------------------------------------------------------------- routes

class Caller(BaseModel):
    org: str | None = None
    name: str | None = None
    phone: str | None = None


class CheckBody(BaseModel):
    story: str = Field(default="", max_length=2000)
    transcript: str = Field(default="", max_length=4000)
    lang: str = "en"
    session_id: str | None = None
    mandate_id: str | None = None
    channel: str = "station"
    caller: Caller = Field(default_factory=Caller)


router = APIRouter()


@router.post("/scam-check")
def scam_check_route(body: CheckBody) -> dict:
    if not (body.story.strip() or body.transcript.strip()):
        raise HTTPException(422, "story or transcript is required")
    return check(body.story, body.lang, session_id=body.session_id, mandate_id=body.mandate_id,
                 channel=body.channel, caller=body.caller.model_dump(), transcript=body.transcript)


@router.get("/scam-check/{check_id}")
def get_check_route(check_id: str) -> dict:
    doc = get_check(check_id)
    if not doc:
        raise HTTPException(404, "unknown check")
    return {**public(doc), "facts_checked": doc.get("facts_checked"), "reported_recently": doc.get("reported_recently"),
            "story": doc.get("story"), "transcript": doc.get("transcript"), "at": doc.get("at"), "channel": doc.get("channel"), "lang": doc.get("lang")}
