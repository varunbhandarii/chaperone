"""The Ask guard's scam radar: POST /scam-check.

    POST /scam-check {session_id, mandate_id, lang, channel: station|line, story?, transcript?,
                      caller: {org, name, phone}}      (story or transcript, or both)
      -> {check_id, decision_id, verdict: scam|unsure|ok, pattern, say, actions[], facts_checked[], sources[],
          cooldown_until, amount, ms, from_cache}
    GET  /scam-checks?mandate_id=&limit=20   Priyank's Safety list (caregiver marker ("scam_checks", "list"))
    GET  /scam-check/{check_id}              one stored check (caregiver marker (check_id, "view"))

Every check is also saved as a decision document (decision "deny" for a scam, "caution" or "noted"
otherwise, never "allow"), so /decisions/{decision_id}/explain answers Priyank's "Why?".

`story` is the voice model's summary; `transcript` is Ruth's exact words. The rules screen both, so a
paraphrase can't hide a hard hit, and Grok sees both.

Order of work:
1. The rule screen. A hard hit answers at once (the say line from the cache or lines.<lang>.json);
   Grok then runs in the background to attach sources to Priyank's alert.
2. Facts from Ruth's own accounts: trusted contacts (mandate v2), the biller balance and
   recent orders. They go to Grok and come back as facts_checked.
3. Grok Responses with x_search and web_search. One budget covers the whole check (RADAR_BUDGET_S,
   11 s): the facts get at most 1.5 s of it and Grok the rest. A Grok answer that arrives late is cached
   for the next check of the same story. Then the cache (sessions/radar_cache.json): by story, the full
   answer; by pattern and language, only the sources and the generic line, never another story's details.
   Then a rules-only verdict.
`say` is at most two sentences and 30 words (36 in Spanish, 45 in Hindi); a longer one is replaced by the fixed
line (sources kept). A pattern's cached entry lends only its sources, never another story's words.

Events: scam_checked; for a scam, caregiver_alerted {check_id, sources} and risk_changed (a 24-hour
card cool-down through policy.risk).

Env: XAI_API_KEY, RADAR_MODEL (grok-4.20-0309-non-reasoning), RADAR_BUDGET_S (the whole check's budget),
RADAR_TIMEOUT_S (12, the background sources call after a hard-rule answer), RADAR_FAKE=1
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
from fastapi import APIRouter, HTTPException, Request
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
BUDGET_S = 11.0  # the whole check; the station and the line give up at 14 s
FACTS_S = 1.5  # facts get at most this slice of it; Grok gets the rest
LATE_LIMIT_S = 30.0  # a Grok answer later than the budget is still cached until this
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
    ("grandparent_secrecy", "grandparent_emergency"), ("family_emergency", "grandparent_emergency"),
    ("code_reading", "gift_card_codes"),
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
    "sentences, at most 30 words in English (36 in Spanish, 45 in Hindi). The first says plainly what this looks like, using a fact from her accounts when there is one. The "
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

# Calls wait on the network, not the CPU: a late answer may hold its thread for LATE_LIMIT_S, so there are enough
# threads that late answers never make a new check wait.
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="radar")
_facts_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="radar-facts")
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
    from policy.postpurchase import order_total_cents, orders_of

    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    out = []
    for doc in load_decisions().values():
        if doc.get("mandate_id") != mandate_id or (doc.get("created_at") or "") < cutoff:
            continue
        for entry in orders_of(doc):  # one per store when the cart was split
            out.append({"at": doc.get("created_at") or "", "total": order_total_cents(doc, entry) / 100,
                        "store": entry.get("merchant") or (doc.get("cart") or {}).get("merchant") or "corner_market"})
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
            if not caller.get("phone"):
                result += "; the caller's number is unknown"
            facts.append({"fact": f"{name}'s number on file ({contact.get('relation', 'contact')})", "result": result})
    if BILLER_WORDS.search(text):
        for biller in mandate.get("billers") or DEMO_BILLERS:
            account = _biller_account(biller["merchant_id"], biller["account_ref"])
            if account:
                try:
                    due = float(account.get("balance_due") or 0)
                except (TypeError, ValueError):
                    due = 0.0
                bits = [f"${due:.2f} due {account.get('due_date')}" if due > 0 else "nothing due",
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

def _parse(resp: dict) -> dict:
    """The verdict and its sources from a Responses API reply; raises RadarError."""
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


def radar(story: str, lang: str, caller: dict, facts: list[dict], hints: list[str], timeout: float,
          transcript: str = "", on_late=None) -> dict:
    """One Grok Responses call with X and web search; raises RadarError.

    If the deadline passes first, `on_late(verdict)` still gets the answer when it arrives, so it can be cached.
    """
    if env("RADAR_FAKE", "0") == "1":
        raise RadarError("RADAR_FAKE=1")
    key = env("XAI_API_KEY")
    if not key:
        raise RadarError("XAI_API_KEY is not set")
    if timeout <= 0.2:
        raise RadarError("no time left in the budget")
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
        # The HTTP call itself may run past the budget, so a late answer can still be cached.
        r = httpx.post(RESPONSES_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=LATE_LIMIT_S)
        r.raise_for_status()
        return r.json()

    future = _pool.submit(call)
    try:
        resp = future.result(timeout=timeout)
    except FutureTimeout as e:
        if on_late:
            def late(f):
                try:
                    on_late(_parse(f.result()))
                except Exception:  # noqa: BLE001 - a late answer is a bonus
                    pass
            future.add_done_callback(late)
        raise RadarError(f"no answer within {timeout:.1f} s") from e
    except Exception as e:  # noqa: BLE001 - HTTP errors, bad JSON
        raise RadarError(f"{type(e).__name__}: {e}") from e
    return _parse(resp)


# ---------------------------------------------------------------- what Ruth hears, amounts

_SENTENCE_END = re.compile(r"[.!?।](?:\s|$)")


SAY_WORDS = {"en": 30, "es": 36, "hi": 45}  # Spanish and Hindi need more words for the same two sentences


def say_ok(say: str, lang: str = "en") -> bool:
    """At most two short sentences and SAY_WORDS words, as the station and phone line speak it."""
    text = (say or "").strip()
    return bool(text) and len(_SENTENCE_END.findall(text + " ")) <= 2 and len(text.split()) <= SAY_WORDS.get(lang, 30)


# Number words, read as whole phrases: "two thousand five hundred" is 2500, "tres mil quinientos" 3500,
# "दो हज़ार पांच सौ" 2500. Values under 100 on their own are not money ("two grandsons", "do not").
_UNITS = {
    # en
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    # es
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "dieciséis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
    # hi (Devanagari and Latin)
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "बीस": 20, "तीस": 30, "चालीस": 40, "पचास": 50, "साठ": 60, "सत्तर": 70, "अस्सी": 80, "नब्बे": 90,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8, "nau": 9,
    "das": 10, "bees": 20, "tees": 30, "chalees": 40, "pachas": 50, "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90,
}
_HUNDREDS = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
             "quinientos": 500, "seiscientos": 600, "setecientos": 700, "ochocientos": 800, "novecientos": 900}
_TIMES_100 = {"hundred", "सौ", "sau"}
_TIMES_1000 = {"thousand", "mil", "हज़ार", "हजार", "hazaar", "hazar", "hajar"}
_ARTICLES = {"a", "un"}  # "a thousand", "un mil": one, only before a scale word
_JOINERS = {"and", "y"}  # "two hundred and fifty", "cuarenta y cinco"
_NOT_MONEY_AFTER = {"times", "veces", "बार", "baar", "people", "personas", "log", "लोग", "years", "años", "saal", "साल"}
_DIGITS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s*(?:dollars?|d[oó]lares|usd|डॉलर|bucks)", re.IGNORECASE)


def _is_number_word(words: list[str], i: int) -> bool:
    w = words[i]
    nxt = words[i + 1] if i + 1 < len(words) else ""
    scale = nxt in _TIMES_100 or nxt in _TIMES_1000
    return (w in _UNITS or w in _HUNDREDS or w in _TIMES_100 or w in _TIMES_1000
            or ((w in _ARTICLES or w.isdigit()) and scale))


def _word_amounts(words: list[str]) -> list[float]:
    found, i = [], 0
    while i < len(words):
        if not _is_number_word(words, i):
            i += 1
            continue
        total = current = 0
        j = i
        while j < len(words):
            w = words[j]
            if w in _JOINERS and j + 1 < len(words) and _is_number_word(words, j + 1):
                j += 1
                continue
            if not _is_number_word(words, j):
                break
            if w in _TIMES_100:
                current = max(current, 1) * 100
            elif w in _TIMES_1000:
                total += max(current, 1) * 1000
                current = 0
            elif w in _HUNDREDS:
                current += _HUNDREDS[w]
            elif w in _ARTICLES:
                current += 1
            else:
                current += int(w) if w.isdigit() else _UNITS[w]
            j += 1
        value = total + current
        after = words[j] if j < len(words) else ""
        if value >= 100 and after not in _NOT_MONEY_AFTER:
            found.append(float(value))
        i = j
    return found


def extract_amount(text: str) -> float | None:
    """The largest dollar amount in a story ("$480", "2000 dólares", "two thousand five hundred dollars",
    "tres mil quinientos", "दो हज़ार")."""
    found = [float((a or b).replace(",", "")) for a, b in _DIGITS.findall(text or "")]
    found += _word_amounts(re.findall(r"[\wऀ-ॿ]+", (text or "").lower()))
    return max(found) if found else None


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


def _fact_line(pattern: str | None, facts: list[dict], lang: str) -> str | None:
    """A fixed scam line that uses her own accounts: the bill the caller threatens is paid, or the relative who
    "called" has a number on file to call back. None when her facts don't answer the story."""
    if pattern in ("utility_shutoff", "utility_impersonation"):
        paid = next((f["fact"].removesuffix(" account") for f in facts
                     if f["fact"].endswith(" account") and str(f.get("result", "")).startswith("nothing due")), None)
        if paid:
            return lines(lang)["scam_check_bill_paid"].format(biller=paid)
    if pattern == "grandparent_emergency":
        name = next((f["fact"].split("'s number")[0] for f in facts
                     if "'s number on file" in f["fact"] and "(daughter)" not in f["fact"]), None)
        if name:
            return lines(lang)["scam_check_family"].format(name=name)
    return None


def pattern_entry(pattern: str | None, lang: str) -> dict | None:
    """What a pattern lends another story: sources and actions, never words. Entries cached before this rule kept
    their story's say ("Alex is in jail..."), which must not be read to Ruth about someone else."""
    entry = cache_get(f"pattern:{pattern}:{lang}") if pattern else None
    return {**entry, "say": None} if entry else None


def remember(v: dict, text: str, lang: str, pattern: str) -> None:
    """Cache a Grok verdict: the full answer for this story; for the pattern, only its sources (never another
    story's details, which the generic line replaces)."""
    say = v.get("say") if say_ok(v.get("say"), lang) else None
    cache_put({"verdict": v["verdict"], "pattern": pattern, "say": say, "actions": v.get("actions") or [],
               "reported_recently": v.get("reported_recently"), "sources": v.get("sources") or []},
              story_key(text, lang))
    if v["verdict"] == "scam" and v.get("sources"):
        cache_put({"verdict": "scam", "pattern": pattern, "say": None, "actions": ["hang_up", "do_not_pay"],
                   "reported_recently": None, "sources": v["sources"]}, f"pattern:{pattern}:{lang}")


def _enrich_in_background(doc: dict, story: str, caller: dict, facts: list[dict], hints: list[str],
                          transcript: str = "") -> None:
    """After a hard-rule answer, fetch sources for Priyank's alert and warm the cache."""
    def run() -> None:
        try:
            v = radar(story, doc["lang"], caller, facts, hints, float(env("RADAR_TIMEOUT_S", "12")), transcript)
        except RadarError:
            v = None
        if v and v.get("verdict") == "scam":
            remember(v, transcript or story, doc["lang"], doc["pattern"])
            doc["sources"] = v["sources"]
            doc["reported_recently"] = v.get("reported_recently")
            save_check(doc)
            _save_decision(doc)
        _alert(doc)

    threading.Thread(target=run, daemon=True).start()


def _alert(doc: dict) -> None:
    post_event("caregiver_alerted", doc["session_id"], doc["mandate_id"], kind="scam_check", check_id=doc["check_id"],
               decision_id=doc["decision_id"], pattern=doc["pattern"], say=doc["say"], sources=doc["sources"],
               amount=doc.get("amount"), cooldown_until=doc["cooldown_until"])


_DECISION = {"scam": "deny", "unsure": "caution", "ok": "noted"}  # never "allow": that word authorizes orders


def _save_decision(doc: dict) -> None:
    """Store the check as a decision document, so /decisions/{id}/explain can answer Priyank's "Why?"."""
    from policy.store import save_decision

    save_decision({
        "decision_id": doc["decision_id"], "decision": _DECISION[doc["verdict"]], "source": "scam_check",
        "check_id": doc["check_id"], "say_key": f"scam_check_{doc['verdict']}",
        "rules": [{"id": f"S_scam_check_{doc['pattern']}", "passed": doc["verdict"] != "scam", "detail": doc["pattern"]}],
        "judge": None, "cart": {"items": [], "total": 0}, "order": None, "approval": None,
        "mandate_id": doc["mandate_id"], "session_id": doc["session_id"], "lang": doc["lang"],
        "ruth_said": (doc.get("transcript") or doc.get("story") or "")[:400], "screen_hits": doc.get("screen_hits") or [],
        "scam_check": {k: doc.get(k) for k in ("verdict", "pattern", "facts_checked", "reported_recently", "sources",
                                               "amount", "cooldown_until")},
        "amount": doc.get("amount"), "created_at": doc["at"],
    })


def _wait(future, seconds: float, default):
    try:
        return future.result(timeout=max(0.0, seconds))
    except Exception:  # noqa: BLE001 - facts are best effort within their slice of the budget
        return default


def check(story: str = "", lang: str = "en", *, session_id: str | None = None, mandate_id: str | None = None,
          channel: str = "station", caller: dict | None = None, transcript: str = "") -> dict:
    start = time.perf_counter()
    deadline = start + float(env("RADAR_BUDGET_S", str(BUDGET_S)))
    story, transcript = (story or "").strip(), (transcript or "").strip()
    story = story or transcript
    heard = " ".join(dict.fromkeys(t for t in (transcript, story) if t))  # what the rules screen
    key_text = transcript or story
    lang = lang if lang in LANG_NAMES else "en"
    mandate_id = mandate_id or DEFAULT_MANDATE_ID
    session_id = session_id or "none"
    caller = {k: (caller or {}).get(k) for k in ("org", "name", "phone")}
    check_id = "sc_" + uuid.uuid4().hex[:10]

    facts_future = _facts_pool.submit(gather_facts, mandate_id, heard, caller)
    screened = screen(heard, lang, session_id=session_id if session_id != "none" else None)
    patterns = [h["pattern"] for h in screened["hits"]]
    hints = sorted(set(patterns))
    hard = screened["action"] == "refuse"
    rule_pattern = _rule_pattern(patterns)
    from_cache = False

    if hard:
        facts = _wait(facts_future, min(FACTS_S, deadline - time.perf_counter()), [])
        pattern = rule_pattern or "gift_card_demand"
        cached = cache_get(story_key(key_text, lang)) or pattern_entry(pattern, lang)
        verdict, from_cache = "scam", bool(cached)
        say = ((cached or {}).get("say") if say_ok((cached or {}).get("say"), lang)
               else _fact_line(pattern, facts, lang) or _fallback("scam", lang))
        raw_actions = (cached or {}).get("actions") or ["hang_up", "do_not_pay"]
        sources = (cached or {}).get("sources") or []
        reported = (cached or {}).get("reported_recently")
    else:
        # Facts get a small slice of the budget; Grok gets what is left.
        facts = _wait(facts_future, min(FACTS_S, deadline - time.perf_counter()), [])
        late = lambda v: remember(v, key_text, lang, _slug(v.get("pattern")) or rule_pattern or "unknown")  # noqa: E731
        try:
            v = radar(story, lang, caller, facts, hints, deadline - time.perf_counter(), transcript, on_late=late)
        except RadarError:
            v = cache_get(story_key(key_text, lang)) or pattern_entry(rule_pattern, lang)
            from_cache = bool(v)
        if v:
            verdict, raw_actions = v["verdict"], v.get("actions") or []
            sources, reported = v.get("sources") or [], v.get("reported_recently")
            grok_pattern = _slug(v.get("pattern"))
            pattern = grok_pattern if grok_pattern not in ("none", "unknown") else (rule_pattern or grok_pattern)
            say = (v.get("say") if say_ok(v.get("say"), lang)
                   else (verdict == "scam" and _fact_line(pattern, facts, lang)) or _fallback(verdict, lang))
            if not from_cache:
                remember(v, key_text, lang, pattern)
        else:
            # Rules only: two or more soft signals are treated as a scam, one as unsure. Screened without the
            # session: after an earlier refusal the session screen says "judge" for anything, even a visit.
            plain = screen(heard, lang)
            verdict = "scam" if plain["action"] == "judge" else "unsure"
            pattern = rule_pattern or "unknown"
            say = (verdict == "scam" and _fact_line(pattern, facts, lang)) or _fallback(verdict, lang)
            raw_actions = ["do_not_pay"]
            sources, reported = [], None

    cooldown_until = None
    if verdict == "scam":
        cooldown_until = risk.set_cooldown(mandate_id, COOLDOWN_HOURS, pattern, check_id, session_id)["cooldown_until"]
    doc = {
        "check_id": check_id, "decision_id": "d_" + uuid.uuid4().hex[:12], "verdict": verdict, "pattern": pattern,
        "say": say, "actions": _actions(raw_actions, verdict, facts), "facts_checked": facts, "sources": sources,
        "reported_recently": reported, "cooldown_until": cooldown_until, "amount": extract_amount(heard),
        "ms": round((time.perf_counter() - start) * 1000), "from_cache": from_cache,
        "session_id": session_id, "mandate_id": mandate_id, "lang": lang, "channel": channel,
        "story": story[:400], "transcript": transcript[:1000], "rule_ids": sorted({h["rule_id"] for h in screened["hits"]}),
        "screen_hits": screened["hits"], "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    save_check(doc)
    _save_decision(doc)
    post_event("scam_checked", session_id, mandate_id, check_id=check_id, decision_id=doc["decision_id"],
               verdict=verdict, pattern=pattern, sources=sources, amount=doc["amount"], ms=doc["ms"], channel=channel)
    if verdict == "scam":
        if hard and not sources:
            _enrich_in_background(doc, story, caller, facts, hints, transcript)
        else:
            _alert(doc)
    return public(doc)


def public(doc: dict) -> dict:
    keys = ("check_id", "decision_id", "verdict", "pattern", "say", "actions", "facts_checked", "sources",
            "cooldown_until", "amount", "ms", "from_cache")
    return {k: doc.get(k) for k in keys}


def summary(doc: dict) -> dict:
    """One row of Priyank's Safety list."""
    return {"check_id": doc["check_id"], "decision_id": doc.get("decision_id"), "verdict": doc["verdict"],
            "pattern": doc["pattern"], "story_excerpt": (doc.get("transcript") or doc.get("story") or "")[:160],
            "say": doc["say"], "sources": doc.get("sources") or [], "amount": doc.get("amount"), "at": doc["at"],
            "channel": doc.get("channel")}


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


def _require_marker(request: Request, marker_id: str, action: str) -> None:
    from policy.approvals import marker_matches

    if not marker_matches(marker_id, request.headers.get("x-chaperone-marker", ""), action):
        raise HTTPException(401, "sign in required")


@router.post("/scam-check")
def scam_check_route(body: CheckBody) -> dict:
    if not (body.story.strip() or body.transcript.strip()):
        raise HTTPException(422, "story or transcript is required")
    return check(body.story, body.lang, session_id=body.session_id, mandate_id=body.mandate_id,
                 channel=body.channel, caller=body.caller.model_dump(), transcript=body.transcript)


@router.get("/scam-checks")
def list_checks_route(request: Request, mandate_id: str = "", limit: int = 20) -> list[dict]:
    """Priyank's Safety list, newest first (caregiver marker for ("scam_checks", "list"))."""
    _require_marker(request, "scam_checks", "list")
    with _lock:
        docs = list(_read(checks_path()).values())
    docs = [d for d in docs if not mandate_id or d.get("mandate_id") == mandate_id]
    docs.sort(key=lambda d: d.get("at") or "", reverse=True)
    return [summary(d) for d in docs[:max(1, min(limit, 100))]]


@router.get("/scam-check/{check_id}")
def get_check_route(check_id: str, request: Request) -> dict:
    """One stored check (caregiver marker for (check_id, "view"))."""
    _require_marker(request, check_id, "view")
    doc = get_check(check_id)
    if not doc:
        raise HTTPException(404, "unknown check")
    return {**public(doc), "reported_recently": doc.get("reported_recently"), "story": doc.get("story"),
            "transcript": doc.get("transcript"), "at": doc.get("at"), "channel": doc.get("channel"), "lang": doc.get("lang")}
