"""The read-only session page behind the receipt's QR code: GET /sessions/{id}?format=html, and its
dispute-ready record, GET /sessions/{id}/record.json.

The caregiver app serves it publicly at https://<tunnel-host>/s/<id>, so the page is self-contained: no
scripts, no links back into the relay (only the record download), and every value is escaped. It reads that
session's ledger file, the merchant's receipts and orders, and the card swipes posted around the session's time
(card events carry no session). It reloads every 30 s (REFRESH_S), slow enough not to disturb reading or a screen
reader. It is written for people: plain words, one colour per meaning (design/tokens.css), and ids and proof
strings only in small monospace lines.
"""

from __future__ import annotations

import datetime
import re
from html import escape as _html_escape


def escape(value) -> str:
    """Text between tags: &, < and > are escaped, so no value can open a tag. Quotes stay readable ("Ruth's")."""
    return _html_escape(str(value), quote=False)


def _attr(value) -> str:
    """A value inside a quoted attribute: quotes are escaped too."""
    return _html_escape(str(value), quote=True)

REFRESH_S = 30
# Every event type the page lists under "What happened", with its plain title (_describe refines it per event).
TITLES = {
    "scam_checked": "Scam check",
    "caution": "Took a closer look",
    "risk_changed": "Extra care on Ruth's card",
    "line_call": "Phone call",
    "bill_checked": "Checked the bill",
    "risk_scored": "Visa risk check",
    "cosigned": "Ruth agreed to the rules",
    "mandate_signed": "Priyank signed the rules",
    "refusal": "Chaperone said no",
    "caregiver_alerted": "Priyank was told",
    "policy_decision": "Checked against Ruth's rules",
    "judge_scored": "Checked for scam signs",
    "approval_requested": "Asked Priyank",
    "approval_result": "Priyank answered",
    "request_signed": "Chaperone signed the order",
    "signature_verified": "The store checked the signature",
    "signature_rejected": "The store rejected a request",
    "payment_link_created": "Order placed",
    "paid": "Paid",
    "receipt_printed": "Receipt",
    "order_status": "Order update",
    "order_cancelled": "Order cancelled, nothing charged",
    "refund_requested": "Return asked",
    "refund_result": "Refund",
    "mandate_paused": "Priyank paused Chaperone",
    "mandate_resumed": "Priyank turned Chaperone back on",
}
STEP_NAMES = {"awaiting_payment": "ordered", "paid": "paid", "preparing": "preparing",
              "ready_for_pickup": "ready for pickup", "picked_up": "picked up", "cancelled": "cancelled",
              "partially_refunded": "partly refunded", "refunded": "refunded"}
ORDER_STEP_TITLES = {"awaiting_payment": "Order placed, waiting for payment", "paid": "Order paid",
                     "preparing": "The store is preparing the order", "ready_for_pickup": "Order ready for pickup",
                     "picked_up": "Order picked up", "cancelled": "Order cancelled",
                     "partially_refunded": "Order partly refunded", "refunded": "Order refunded"}
# Order fields that stay off the public page and record: the pickup code works at the counter, and the
# payment link is payable.
PRIVATE_ORDER_FIELDS = {"pickup_code", "checkout_url", "card_auth"}

# Plain words for the ids the services log. Anything unknown falls back to its words without underscores.
RULE_WORDS = {
    "R0": "the rules were not in force",
    "R1": "a kind of purchase Ruth's rules never allow",
    "R2": "a store outside the rules",
    "R3": "a category outside the rules",
    "R4": "over the per-purchase limit",
    "R5": "over the monthly limit",
    "R6": "above the ask-Priyank amount",
    "R7": "flagged by the scam check",
}
PATTERN_WORDS = {
    "grandparent_emergency": "a grandchild in trouble",
    "utility_shutoff": "a power shut-off threat",
    "utility": "a power shut-off threat",
    "utility_impersonation": "a caller posing as the power company",
    "refund_overpayment": "a refund overpayment",
    "refund_fee": "a fee to get a refund",
    "recovery_scam": "a promise to get lost money back",
    "tech_support": "fake tech support",
    "parcel_customs": "a parcel held at customs",
    "fake_delivery": "a fake delivery fee",
    "digital_arrest": "a fake arrest threat",
    "fake_renewal": "a fake renewal charge",
    "bank_impersonation": "a caller posing as the bank",
    "government_impersonation": "a caller posing as the government",
    "safe_account": "a request to move money to a safe account",
    "crypto_atm": "a request to pay at a crypto ATM",
    "courier_pickup": "a courier coming to collect money",
    "gift_card_codes": "a request to read out gift card codes",
    "gift_card_demand": "a demand for gift cards",
}
VERDICT_WORDS = {"scam": "a scam", "likely_scam": "likely a scam", "unsure": "not sure", "unclear": "not sure",
                 "suspicious": "not sure", "ok": "looks fine", "safe": "looks fine", "likely_safe": "looks fine"}
METHOD_WORDS = {"passkey": "with his passkey", "code": "with the backup code", "timeout": "no answer in time",
                "cancelled": "cancelled", "voice": "by voice"}
RISK_WORDS = {"ACCEPT": "accept", "ACCEPTED": "accept", "REJECT": "reject", "REJECTED": "reject",
              "REVIEW": "review", "PENDING_REVIEW": "review", "AUTHORIZED_PENDING_REVIEW": "review"}
REFUND_WORDS = {"TRANSMITTED": "sent to the card", "PENDING": "on its way"}
CHECK_NAMES = {"content_digest": "Body unchanged", "signature": "Signature valid", "window": "Fresh",
               "nonce": "Never used before", "decision": "Matches Chaperone's decision",
               "storefront": "A store Ruth may buy from", "cybersource_webhook": "Payment notice signed"}
CARD_REASONS = {"card_cooldown": "Ruth's card is on a cool-down after a scam check.",
                "card_blocked_category": "A kind of store that is always blocked.",
                "card_atm_cap": "Over the daily cash limit.",
                "card_unusual_amount": "Far more than Ruth usually spends there.",
                "card_over_cap": "Over the limit for that store."}
CHANNEL_WORDS = {"station": "at home", "line": "on the phone line"}
LANG_NAMES = {"en": "English", "es": "Spanish", "hi": "Hindi"}

# One meaning per colour (design/tokens.css): tone -> badge class, rail dot colour.
TONES = {
    "prot": ("ch-badge--protected", "var(--ch-blue)"),
    "ok": ("ch-badge--ok", "var(--ch-ok)"),
    "wait": ("ch-badge--wait", "var(--ch-wait)"),
    "info": ("ch-badge--info", "var(--ch-info)"),
    "err": ("ch-badge--err", "var(--ch-err)"),
    "plain": ("", "var(--ch-field)"),
}
# Stroke icons on a 24 grid, open shapes, in the text colour of where they sit.
ICONS = {
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "cross": '<path d="M6 6l12 12M18 6L6 18"/>',
    "dash": '<path d="M6 12h12"/>',
    "shield": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "alert": '<path d="M12 4l9 16H3L12 4z"/><path d="M12 10v4M12 17h.01"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
}
TONE_ICONS = {"prot": "shield", "ok": "check", "wait": "clock", "info": "info", "err": "alert", "plain": "dash"}


def _icon(name: str, size: int = 14, stroke: float = 2.4) -> str:
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
            f'focusable="false">{ICONS[name]}</svg>')


def _money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return ""


def _at_ms(ms) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _at_iso(at) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(str(at)).astimezone()
    except (ValueError, OverflowError, OSError):
        return None


def _hms(moment: datetime.datetime | None) -> str:
    """2:50:57, on the 12-hour clock; the page's date line gives AM or PM."""
    return f"{moment.hour % 12 or 12}:{moment:%M:%S}" if moment else ""


def _hm(moment: datetime.datetime) -> str:
    return f"{moment.hour % 12 or 12}:{moment:%M} {moment:%p}"


def _day(moment: datetime.datetime) -> str:
    return f"{moment:%b} {moment.day}, {moment.year}"


def _time(ms) -> str:
    return _hms(_at_ms(ms))


def _clock(at) -> str:
    return _hms(_at_iso(at))


def _event_ms(e: dict):
    return e.get("rt") or e.get("t")


def _words(value) -> str:
    """A slug or enum in plain words: no underscores, lower case."""
    return str(value or "").replace("_", " ").strip().lower()


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _rule(rule_id) -> str:
    rid = str(rule_id or "")
    number = re.match(r"(R\d+)(?:_|$)", rid)
    if number and number.group(1) in RULE_WORDS:
        return RULE_WORDS[number.group(1)]
    if rid.startswith("S_scam_check"):
        return RULE_WORDS["R7"]
    if rid.startswith("S_"):
        return "scam words in the request"
    return _words(re.sub(r"^R_", "", rid))


def _rules(ids) -> str:
    return " and ".join(dict.fromkeys(_rule(r) for r in ids or [] if r))


def _pattern(slug) -> str:
    slug = str(slug or "")
    if slug in ("", "none", "unknown"):
        return ""
    return PATTERN_WORDS.get(slug) or _words(slug)


def _pickup_words(pickup, *codes) -> str:
    """The receipt's pickup note ("after 3pm"). The pickup code works at the counter, so a note that is only
    digits, or that contains a code, is never shown."""
    text = str(pickup or "").strip()
    if not re.search(r"[^\W\d_]", text) or any(code and str(code) in text for code in codes):
        return ""
    return text


def _card(last4) -> str:
    return f"Visa ending {last4}" if last4 else ""


def _join(*parts) -> str:
    return " · ".join(str(p) for p in parts if p)


def transcript(events: list[dict]) -> list[dict]:
    """One line per spoken turn: a later heard event with the same item_id replaces the earlier one."""
    turns: dict[str, dict] = {}
    for i, e in enumerate(events):
        if e.get("type") == "heard" and (e.get("text") or e.get("transcript")):
            turns[str(e.get("item_id") or f"#{i}")] = e
    return sorted(turns.values(), key=lambda e: e.get("seq", 0))


def _spoken(events: list[dict]) -> list[tuple[dict, str]]:
    """The lines to show under "What was said". The phone line posts Ruth's words without an item_id, and its
    voice builder sometimes resends the whole turn so far ("I need water." then "I need water. And bread."): a line
    without an item_id that only extends the line right before it, same speaker, replaces it on the page. The
    dispute record keeps every line as logged (transcript())."""
    lines: list[tuple[dict, str]] = []
    for e in transcript(events):
        text = str(e.get("text") or e.get("transcript") or "")
        if lines:
            last, last_text = lines[-1]
            if (not e.get("item_id") and not last.get("item_id") and last.get("role") == e.get("role")
                    and len(text) > len(last_text) and text.startswith(last_text)):
                lines[-1] = (e, text)
                continue
        lines.append((e, text))
    return lines


def orders(events: list[dict]) -> list[dict]:
    """Every order in the session, oldest first, with whether it was paid or cancelled."""
    found: dict[str, dict] = {}
    for e in events:
        order_id = e.get("order_id")
        if not order_id or e.get("type") not in ("payment_link_created", "paid", "order_cancelled"):
            continue
        order = found.setdefault(str(order_id), {"order_id": str(order_id), "amount": None, "paid": False, "via": None,
                                                 "cancelled": False, "store": None})
        order["store"] = order["store"] or e.get("store")
        if e["type"] == "payment_link_created":
            order["amount"] = e.get("amount")
        elif e["type"] == "order_cancelled":
            order["cancelled"] = True
        else:
            order["paid"], order["via"] = True, e.get("via")
            order["amount"] = order["amount"] or e.get("total", e.get("amount"))
    return list(found.values())


def _paid_via(via) -> str:
    via = str(via or "")
    if via.startswith("cybersource"):
        return "signed payment notice"
    if via in ("host_confirmed", "host"):
        return "confirmed by the host"
    if via == "mock":
        return "mock page"
    return _words(via)


def _describe(e: dict, stores: dict[str, str]) -> tuple[str, str, str, str, str]:
    """(tone, badge word, title, plain detail, mono detail) for one event row under "What happened"."""
    kind = e.get("type")
    title = TITLES[kind]
    store = e.get("store") or stores.get(str(e.get("order_id") or "")) or ""
    if kind == "scam_checked":
        verdict = str(e.get("verdict") or "")
        sources = len(e.get("sources") or [])
        detail = _join(_cap(_pattern(e.get("pattern"))),
                       f"{_money(e.get('amount'))} asked for" if _money(e.get("amount")) else "",
                       CHANNEL_WORDS.get(str(e.get("channel") or ""), ""),
                       f"{sources} source{'s' if sources != 1 else ''}" if sources else "")
        if verdict in ("scam", "likely_scam"):
            return "prot", "Protected", f"Scam check found {VERDICT_WORDS[verdict]}", detail, ""
        if verdict in ("ok", "safe", "likely_safe"):
            return "info", "Checked", "Scam check found no scam", detail, ""
        return "wait", "Extra care", f"Scam check: {VERDICT_WORDS.get(verdict) or _words(verdict) or 'not sure'}", detail, ""
    if kind == "caution":
        words = [e.get("words")] if isinstance(e.get("words"), str) else e.get("words") or []
        heard = ", ".join(f"“{w}”" for w in words[:3])
        return "wait", "Extra care", title, f"Heard {heard}" if heard else "", ""
    if kind == "risk_changed":
        if not e.get("cooldown_until"):
            return "info", "Ended", "Extra care on Ruth's card ended", _cap(_words(e.get("reason"))), ""
        until = _at_iso(e.get("cooldown_until"))
        reason = _pattern(e.get("reason"))
        return "wait", "Extra care", title, _join(f"Until {_day(until)}, {_hm(until)}" if until else "",
                                                  f"after a scam check: {reason}" if reason else ""), ""
    if kind == "line_call":
        phase = str(e.get("phase") or "")
        try:
            seconds = round(float(e.get("seconds")))
        except (TypeError, ValueError):
            seconds = None
        lasted = (f"Lasted {seconds // 60} min {seconds % 60} s" if seconds and seconds >= 60
                  else f"Lasted {seconds} s" if seconds else "")
        if phase == "ended":
            return "info", "Ended", "Phone call ended", lasted, ""
        if phase in ("started", "start", "answered", "ringing"):
            return "info", "Call", "Ruth called the Chaperone Line", lasted, ""
        return "info", "Call", _join("Phone call", _words(phase)), lasted, ""
    if kind == "bill_checked":
        due = _at_iso(e.get("due_date"))
        due_text = _day(due) if due else str(e.get("due_date") or "")
        return "info", "Checked", title, _join(e.get("biller"), f"{_money(e.get('balance_due'))} due {due_text}".strip(),
                                               "past due" if e.get("past_due") else "not past due"), ""
    if kind == "risk_scored":
        score = f"score {e['score']}" if e.get("score") not in (None, "") else ""
        if e.get("error") and not e.get("status"):
            return "err", "Can't reach", "Visa risk check unavailable", _join(store), ""
        word = RISK_WORDS.get(str(e.get("status") or "").upper()) or _words(e.get("status"))
        tone, badge = {"accept": ("ok", "Passed"), "reject": ("prot", "Protected"),
                       "review": ("wait", "Review")}.get(word, ("info", "Checked"))
        return tone, badge, f"Visa risk check: {word}" if word else title, _join(store, score), ""
    if kind == "cosigned":
        said = f"“{e['said']}”" if e.get("said") else ""
        return "ok", "Agreed", title, _join(_cap(METHOD_WORDS.get(str(e.get("method") or "voice"))
                                                 or f"by {_words(e.get('method'))}"), said), ""
    if kind == "mandate_signed":
        return "ok", "Signed", title, "With his passkey", ""
    if kind == "refusal":
        ids = e.get("rule_ids") or [e.get("rule_id") or e.get("rule") or ""]
        lang = LANG_NAMES.get(str(e.get("lang") or ""))
        return "prot", "Protected", title, _join(_cap(_rules(ids)), f"said in {lang}" if lang else ""), ""
    if kind == "caregiver_alerted":
        about = {"screen_refusal": "About a request Chaperone refused",
                 "scam_check": f"About a scam call{' asking for ' + _money(e.get('amount')) if _money(e.get('amount')) else ''}",
                 "refund": "About a return"}.get(str(e.get("kind") or ""), "")
        return "info", "Told", title, about, ""
    if kind == "policy_decision":
        failed = e.get("rules_failed")
        if failed is None:
            failed = [r.get("id") for r in e.get("rules") or [] if isinstance(r, dict) and r.get("passed") is False]
        decision = str(e.get("decision") or "").lower()
        total = _money(e.get("total"))
        mono = f"decision {e['decision_id']}" if e.get("decision_id") else ""
        if decision == "allow":
            return "ok", "Passed", "Inside Ruth's rules", _join(total, "every rule passed"), mono
        if decision == "approve":
            return "wait", "Needs Priyank", "Asked Priyank first", _join(total, _rules(failed)), mono
        if decision == "deny":
            # a limit is information (Handoff table); a blocked kind of purchase or a scam is Chaperone protecting
            limits_only = bool(failed) and all(re.match(r"R[45](_|$)", str(r)) for r in failed)
            tone, badge = ("info", "Over a limit") if limits_only else ("prot", "Protected")
            return tone, badge, "Outside Ruth's rules", _join(total, _rules(failed)), mono
        return "info", "Checked", title, _join(total, _words(decision)), mono
    if kind == "judge_scored":
        action = str(e.get("action") or "")
        try:
            score = float(e.get("scam_score"))
        except (TypeError, ValueError):
            score = None
        found = ", ".join(p for p in (_pattern(p) for p in e.get("patterns") or []) if p)
        score_text = f"score {e.get('scam_score')}" if score is not None else ""
        if action in ("proceed", "allow") or (not action and score is not None and score < 0.4):
            return "info", "Checked", "Checked: no scam signs", _join("Low risk", score_text, found), ""
        if action == "refuse_and_alert":
            return "prot", "Protected", "Checked: scam signs", _join(_cap(score_text), found), ""
        return "wait", "Extra care", "Checked: some scam signs", _join(_cap(score_text), found), ""
    if kind == "approval_requested":
        return "wait", "Needs Priyank", title, _join(_money(e.get("amount")), e.get("reason") or _rule(e.get("rule"))), ""
    if kind == "approval_result":
        method = str(e.get("method") or "")
        how = METHOD_WORDS.get(method) or _words(method)
        if e.get("approved"):
            return "ok", "Approved", "Priyank approved", _cap(how), ""
        if method == "timeout":
            return "info", "No answer", "Priyank did not answer in time", "Nothing was bought", ""
        if method == "cancelled":
            return "info", "Cancelled", "The request was cancelled", "Nothing was bought", ""
        return "prot", "Protected", "Priyank said no", _join(_cap(how), "nothing was bought"), ""
    if kind == "request_signed":
        return "ok", "Signed", title, "", _join(f"key {e['keyid']}" if e.get("keyid") else "",
                                               f"nonce {e['nonce']}" if e.get("nonce") else "")
    if kind in ("signature_verified", "signature_rejected"):
        checks = [c for c in e.get("checks") or [] if isinstance(c, dict)]
        passed = sum(1 for c in checks if c.get("passed"))
        who = store or "The store"
        if kind == "signature_verified":
            return "ok", "Verified", f"{who} checked the signature", f"{passed} of {len(checks)} checks passed", ""
        bad = [c for c in checks if c.get("passed") is False]
        # A store refusing a request it cannot trust (a replay, a used decision) is protection, not a failure;
        # only a payment-notice key that is not set up is a system problem.
        broken = any(c.get("id") == "cybersource_webhook" and "not configured" in str(c.get("detail")) for c in bad)
        tone, badge = ("err", "Can't reach") if broken else ("prot", "Protected")
        names = ", ".join((CHECK_NAMES.get(str(c.get("id"))) or _words(c.get("id"))).lower() for c in bad)
        return (tone, badge, f"{who} rejected a request",
                _join(f"{passed} of {len(checks)} checks passed", f"failed: {names}" if names else ""),
                " · ".join(str(c.get("detail")) for c in bad if c.get("detail")))
    if kind == "payment_link_created":
        how = "Visa sandbox payment link" if e.get("backend") == "visa" else "mock payment page"
        return "plain", "Ordered", title, _join(store, _money(e.get("amount")), how), str(e.get("order_id") or "")
    if kind == "paid":
        return "ok", "Paid", f"Paid {_money(e.get('total', e.get('amount')))}".strip(), _join(store, _paid_via(e.get("via"))), ""
    if kind == "receipt_printed":
        return "info", "Receipt", "Receipt shown on screen" if e.get("via") == "screen" else "Receipt printed", store, ""
    if kind == "order_status":
        status = str(e.get("status") or "")
        step = STEP_NAMES.get(status) or _words(status)
        tone = "ok" if status in ("paid", "ready_for_pickup", "picked_up", "refunded", "partially_refunded") else "plain"
        heading = ORDER_STEP_TITLES.get(status) or (f"Order {step}" if step else title)
        return tone, _cap(step) or "Update", heading, store, ""  # never the pickup code the event carries
    if kind == "order_cancelled":
        link = str(e.get("link_status") or "")
        link_words = "switched off" if link.upper() == "INACTIVE" else _words(link)
        return "info", "Cancelled", title, _join(store, _money(e.get("amount")),
                                                 f"payment link {link_words}" if link_words else ""), ""
    if kind == "refund_requested":
        what = e.get("name") or e.get("sku") or ""
        return "info", "Asked", title, _join(f"{e.get('qty', 1)} × {what}" if what else "", _money(e.get("amount"))), ""
    if kind == "refund_result":
        status = str(e.get("status") or "").upper()
        amount = _money(e.get("amount"))
        tone, badge = {"TRANSMITTED": ("ok", "Refunded"), "PENDING": ("info", "On its way")}.get(
            status, ("err", "Failed") if status in ("FAILED", "DECLINED", "ERROR", "REJECTED") else ("info", "Refund"))
        words = REFUND_WORDS.get(status) or _words(status)
        return (tone, badge, " ".join(p for p in ("Refund", amount, words) if p),
                _join(_card(e.get("card_last4")), "sandbox processor stub"), "")
    if kind == "mandate_paused":
        return "wait", "Paused", title, "Nothing can be bought", ""
    if kind == "mandate_resumed":
        how = METHOD_WORDS.get(str(e.get("method") or "")) or _words(e.get("method"))
        return "ok", "Resumed", title, _cap(how), ""
    return "info", "Checked", title, "", ""


def _el(tag: str, cls: str, text) -> str:
    """<tag class="cls">text</tag> with the text escaped, or nothing when there is no text."""
    return f'<{tag} class="{cls}">{escape(str(text))}</{tag}>' if text else ""


def _wrap(tag: str, cls: str, inner_html: str) -> str:
    """<tag class="cls"> around markup that is already escaped, or nothing when it is empty."""
    return f'<{tag} class="{cls}">{inner_html}</{tag}>' if inner_html else ""


def _badge(tone: str, word: str) -> str:
    cls = TONES.get(tone, TONES["info"])[0]
    return f'<span class="ch-badge {cls}">{_icon(TONE_ICONS.get(tone, "info"), 12, 2.6)}{escape(word)}</span>'


def _happened(events: list[dict]) -> str:
    stores = {o["order_id"]: str(o["store"]) for o in orders(events) if o.get("store")}
    rows = []
    for e in events:
        if e.get("type") not in TITLES:
            continue
        tone, badge, title, detail, mono = _describe(e, stores)
        rows.append(
            f'<li><span class="tl-t">{escape(_time(_event_ms(e)))}</span>'
            f'<span class="tl-rail" aria-hidden="true"><span class="tl-dot" style="background:{TONES.get(tone, TONES["info"])[1]}"></span></span>'
            f'<div class="tl-body"><div class="tl-h"><span class="tl-title">{escape(title)}</span>{_badge(tone, badge)}</div>'
            f'{_el("div", "tl-d", detail)}{_el("div", "mono-s", mono)}</div></li>')
    body = f'<ol class="tl">{"".join(rows)}</ol>' if rows else '<p class="empty">Nothing has happened yet.</p>'
    return f'<section class="card" aria-labelledby="h-happened"><h2 id="h-happened">What happened</h2>{body}</section>'


def _said(events: list[dict]) -> str:
    lines = "".join(
        f'<li class="{"agent" if e.get("role") in ("agent", "assistant") else "shopper"}">'
        f'<span class="who">{"Chaperone" if e.get("role") in ("agent", "assistant") else "Ruth"}</span>'
        f'<span class="said">{escape(text)}</span></li>'
        for e, text in _spoken(events))
    body = f'<ul class="turns">{lines}</ul>' if lines else '<p class="empty">No words recorded.</p>'
    return f'<section class="card" aria-labelledby="h-said"><h2 id="h-said">What was said</h2>{body}</section>'


def _circle(tone: str) -> str:
    return f'<span class="circ circ-{tone}" aria-hidden="true">{_icon(TONE_ICONS[tone], 20, 2.8)}</span>'


def _status(events: list[dict]) -> str:
    """The top of the page: what Chaperone stopped (blue, never red), then each order's outcome."""
    told = any(e.get("type") == "caregiver_alerted" for e in events)
    stopped = []
    for e in events:
        if e.get("type") == "scam_checked" and e.get("verdict") in ("scam", "likely_scam"):
            amount = _money(e.get("amount"))
            note = _join(_cap(_pattern(e.get("pattern"))), CHANNEL_WORDS.get(str(e.get("channel") or ""), ""))
            stopped.append(
                f'<div class="st">{_circle("prot")}<div class="st-main"><div class="st-what">Scam stopped</div>'
                f'{_el("div", "st-note", note)}'
                f'{_el("span", "ch-badge ch-badge--money", f"{amount} kept safe" if amount else "")}</div></div>')
        elif e.get("type") == "refusal":
            reasons = _rules(e.get("rule_ids") or [e.get("rule_id") or e.get("rule") or ""])
            note = " ".join(p for p in (_cap(reasons) + "." if reasons else "", "Priyank was told." if told else "") if p)
            stopped.append(
                f'<div class="st">{_circle("prot")}<div class="st-main"><div class="st-what">Chaperone said no</div>'
                f'{_el("div", "st-note", note)}</div></div>')
    protected = (f'<section class="card prot" aria-labelledby="h-prot"><h2 id="h-prot" class="prot-h">'
                 f'{_icon("shield", 18, 2.4)}Protected</h2>{"".join(stopped)}</section>') if stopped else ""

    rows = []
    for o in orders(events):
        store = str(o.get("store") or "")
        if o["paid"]:
            tone, what = "ok", f"Paid at {store}" if store else "Paid"
        elif o.get("cancelled"):
            tone, what = "plain", f"Cancelled at {store}, nothing charged" if store else "Cancelled, nothing charged"
        else:
            tone, what = "wait", f"Waiting for payment at {store}" if store else "Waiting for payment"
        rows.append(f'<div class="st">{_circle(tone)}<div class="st-main"><div class="st-what">{escape(what)}</div>'
                    f'<div class="mono-s">{escape(o["order_id"])}</div></div>'
                    f'<span class="st-amt ch-num">{escape(_money(o["amount"]))}</span></div>')
    if not rows:
        rows.append(f'<div class="st">{_circle("plain")}<div class="st-main">'
                    f'<div class="st-what">No order in this session</div></div></div>')
    return protected + f'<section class="card" aria-label="Orders in this session">{"".join(rows)}</section>'


def _order_card(order: dict, receipt: dict | None) -> str:
    """One card per order the merchant holds: its steps, what was bought, any refund. Never the pickup code or
    the payment link."""
    order_id = str(order.get("order_id", ""))
    store = str(order.get("store") or "")
    timeline = order.get("timeline") or []
    steps = []
    for i, t in enumerate(timeline):
        status = str(t.get("status") or "")
        name = _cap(STEP_NAMES.get(status) or _words(status))
        last = i == len(timeline) - 1 and status not in ("awaiting_payment", "cancelled")
        steps.append(f'<li class="ch-badge{" ch-badge--ok" if last else ""}">{escape(name)}'
                     f' <span class="step-t">{escape(_clock(t.get("at")))}</span></li>')
    items = (receipt or {}).get("items") or [
        {"qty": line.get("qty", 1), "name": line.get("name", ""),
         "price": (float(line.get("unit_price") or 0) * float(line.get("qty") or 1)) if line.get("unit_price") else None}
        for line in order.get("lines") or [] if isinstance(line, dict)]
    lines = "".join(
        f'<li><span>{escape(str(i.get("qty", 1)))} × {escape(str(i.get("name", "")))}</span>'
        f'<span class="ch-num">{escape(_money(i.get("price")))}</span></li>' for i in items)
    total = (receipt or {}).get("total")
    if receipt and lines:
        lines += f'<li class="total"><span>Total</span><span class="ch-num">{escape(_money(total))}</span></li>'
    refunds = "".join(
        f'<li class="refund">{_icon("check", 16, 2.8)}<div><div class="refund-h">Refund {escape(_money(r.get("amount")))}'
        f' · {escape(str(r.get("qty", 1)))} × {escape(str(r.get("name", "")))}</div>'
        f'{_el("div", "tl-d", _join(REFUND_WORDS.get(str(r.get("status") or "").upper()) or _words(r.get("status")), _card(r.get("card_last4")), "sandbox processor stub"))}'
        f'</div></li>'
        for r in order.get("refunds") or [] if isinstance(r, dict))
    paid = order.get("paid_at") or str(order.get("status") or "") not in ("", "awaiting_payment", "cancelled")
    pickup = _pickup_words((receipt or {}).get("pickup"), order.get("pickup_code"), (receipt or {}).get("pickup_code"))
    extra = _join(f"Paid with {_card(order.get('card_last4'))}" if paid and order.get("card_last4") else "",
                  f"Pickup {pickup}" if pickup else "")
    ids = _join(order_id, f'decision {receipt["decision_id"]}' if receipt and receipt.get("decision_id") else "")
    title = f"{store + ' · ' if store else ''}{_money(order.get('amount'))}"
    return (f'<section class="card order" id="order-{_attr(order_id)}" aria-label="{_attr(title)}">'
            f'<h2 class="order-h">{escape(title)}</h2>{_el("div", "mono-s", ids)}'
            f'{_wrap("ol", "steps", "".join(steps))}{_wrap("ul", "lines", lines)}'
            f'{_el("p", "tl-d", extra)}{_wrap("ul", "refunds", refunds)}</section>')


def _receipt(receipt: dict) -> str:
    """A receipt the merchant printed for an order that has no card of its own on this page."""
    items = "".join(
        f'<li><span>{escape(str(i.get("qty", 1)))} × {escape(str(i.get("name", "")))}</span>'
        f'<span class="ch-num">{escape(_money(i.get("price")))}</span></li>' for i in receipt.get("items") or [])
    pickup = _pickup_words(receipt.get("pickup"), receipt.get("pickup_code"))
    where = _join(receipt.get("store") or receipt.get("merchant"), f"pickup {pickup}" if pickup else "")
    return (f'<section class="card" aria-label="Receipt"><h2>Receipt</h2><ul class="lines">{items}'
            f'<li class="total"><span>Total</span><span class="ch-num">{escape(_money(receipt.get("total")))}</span></li></ul>'
            f'{_el("p", "tl-d", where)}'
            f'<div class="mono-s">order {escape(str(receipt.get("order_id", "")))} · decision '
            f'{escape(str(receipt.get("decision_id", "")))}</div></section>')


def _proof(events: list[dict]) -> str:
    # The check that let the order through; later rejections (a replayed order) are still listed in the steps.
    verified = next((e for e in reversed(events) if e.get("type") == "signature_verified"), None) \
        or next((e for e in reversed(events) if e.get("type") == "signature_rejected"), None)
    if not verified:
        return ""
    signed = next((e for e in reversed(events) if e.get("type") == "request_signed"), None)
    nonce = verified.get("nonce") or (signed or {}).get("nonce") or ""
    keyid = verified.get("keyid") or (signed or {}).get("keyid") or ""
    checks = [c for c in verified.get("checks") or [] if isinstance(c, dict)]
    rows = []
    for c in checks:
        state = "ok" if c.get("passed") else "no" if c.get("passed") is False else "unk"
        icon = _icon({"ok": "check", "no": "cross", "unk": "dash"}[state], 18, 3)
        name = CHECK_NAMES.get(str(c.get("id"))) or _cap(_words(c.get("id")))
        verdict = {"ok": "passed", "no": "failed", "unk": "not checked"}[state]
        nonce_line = _el("div", "mono-s", f"nonce {nonce}" if c.get("id") == "nonce" and nonce else "")
        rows.append(f'<li class="pf pf-{state}">{icon}<div class="pf-b"><div class="pf-n">{escape(name)}'
                    f'<span class="vh"> ({verdict})</span></div>'
                    f'{_el("div", "mono-s", c.get("detail"))}{nonce_line}</div></li>')
    passed = sum(1 for c in checks if c.get("passed"))
    all_ok = verified.get("type") == "signature_verified" and checks and passed == len(checks)
    badge = _badge("ok", f"{passed} of {len(checks)} passed") if all_ok else _badge("prot", "Store refused it")
    ids = _join(f"key {keyid}" if keyid else "", f"decision {verified['decision_id']}" if verified.get("decision_id") else "")
    return (f'<section class="card" aria-labelledby="h-proof"><div class="sec-h"><h2 id="h-proof">Proof the order came '
            f'from Chaperone</h2>{badge}</div>{_wrap("ul", "proof", "".join(rows))}{_el("div", "mono-s", ids)}</section>')


def _vtc_for(card_events: list[dict] | None, swipe: dict) -> dict | None:
    token = swipe.get("token")
    return next((e for e in card_events or [] if e.get("type") == "vtc_decision" and token and e.get("token") == token
                 and e.get("should_decline") is not None), None)


def _swipes(card_events: list[dict] | None) -> str:
    rows = []
    for e in card_events or []:
        when = f'<span class="tl-t">{escape(_time(_event_ms(e)))}</span>'
        if e.get("type") == "card_decision":
            ok = e.get("result") == "approved"
            badge = (f'<span class="ch-badge {"ch-badge--ok" if ok else "ch-badge--protected"}">'
                     f'{_icon("check" if ok else "shield", 12, 2.6)}{"Approved" if ok else "Declined"} '
                     f'{escape(_money(e.get("amount")))}</span>')
            reason = "" if ok else (e.get("reason") or CARD_REASONS.get(str(e.get("reason_key") or ""), ""))
            vtc = _vtc_for(card_events, e)
            chip = (f'<span class="ch-badge ch-badge--visa">Visa VTC · {"decline" if vtc.get("should_decline") else "approve"}'
                    f'</span>') if vtc else ""
            rows.append(f'<li>{when}<div class="sw-b"><div class="tl-h">{badge}{chip}</div>'
                        f'{_el("div", "tl-d", _join(e.get("store"), _card(e.get("card_last4"))))}'
                        f'{_el("div", "tl-d", reason)}</div></li>')
        elif e.get("type") == "card_hold_released":
            detail = (f'up to {_money(e.get("max_amount"))}{" at " + str(e.get("store")) if e.get("store") else ""}'
                      f', for 10 minutes')
            rows.append(f'<li>{when}<div class="sw-b"><div class="tl-h"><span class="ch-badge ch-badge--ok">'
                        f'{_icon("check", 12, 2.6)}Priyank allowed it once</span></div>'
                        f'<div class="tl-d">{escape(_cap(detail))}</div></div></li>')
    if not rows:
        return ""
    return (f'<section class="card" aria-labelledby="h-card"><h2 id="h-card">Ruth\'s card</h2>'
            f'<p class="sub">Swipes during the extra care that followed the scam check.</p>'
            f'<ul class="swipes">{"".join(rows)}</ul></section>')


def dispute_record(session_id: str, events: list[dict], orders_: list[dict], mandate: dict | None,
                   card_events: list[dict] | None = None) -> dict:
    """Everything an issuer needs to settle a dispute: the shopper's words, the signed mandate, each policy
    decision, the scam checks, the RFC 9421 signature and the merchant's checks, the Visa risk score, the
    payment and any refund, and the card swipes around the session with Visa VTC's answer."""
    card_events = card_events or []
    stores = sorted({str(o.get("store")) for o in orders_ if o.get("store")}
                    | {str(e.get("store")) for e in events if e.get("type") == "payment_link_created" and e.get("store")})
    def pick(e: dict, *keys) -> dict:
        return {"at_ms": e.get("rt") or e.get("t"), **{k: e.get(k) for k in keys if e.get(k) is not None}}

    return {
        "kind": "chaperone.dispute_record.v1",
        "session_id": session_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "merchant": f"{', '.join(stores) or 'no store'} (Visa sandbox, no real money)",
        "stores": stores,
        "shopper_words": [pick(e, "text", "lang") for e in transcript(events)
                          if e.get("role") not in ("agent", "assistant")],
        # the credential id is shortened: it identifies the caregiver's passkey, and the record is downloadable
        "mandate": mandate and {**mandate, "credential_id": (str(mandate.get("credential_id") or "")[:16] or None)},
        "decisions": [pick(e, "decision_id", "decision", "rules_failed", "total", "say_key")
                      for e in events if e.get("type") == "policy_decision"],
        "refusals": [pick(e, "rule_id", "rule_ids", "spoken_key", "lang")
                     for e in events if e.get("type") == "refusal"],
        "scam_checks": [pick(e, "check_id", "verdict", "pattern", "sources", "amount", "channel", "ms")
                        for e in events if e.get("type") == "scam_checked"],
        "risk_scores": [pick(e, "order_id", "merchant", "store", "status", "score", "risk_id")
                        for e in events if e.get("type") == "risk_scored"],
        "card_decisions": [pick(e, "type", "token", "store", "mcc", "amount", "result", "reason_key", "hold_id",
                                "should_decline", "rule", "max_amount", "allowed_until") for e in card_events],
        "cosign": [pick(e, "by", "method", "said", "lang", "mandate_hash") for e in events if e.get("type") == "cosigned"],
        "approvals": [pick(e, "type", "approval_id", "amount", "rule", "approved", "method")
                      for e in events if e.get("type") in ("approval_requested", "approval_result")],
        "signatures": [pick(e, "type", "action", "keyid", "nonce", "created", "expires", "decision_id", "order_id", "checks")
                       for e in events if e.get("type") in ("request_signed", "signature_verified", "signature_rejected")],
        "orders": [{k: v for k, v in o.items() if k not in PRIVATE_ORDER_FIELDS} | {
                       "payment_link": {"backend": (o.get("payment_link") or {}).get("backend")}}
                   for o in orders_],
        "payments": [pick(e, "order_id", "total", "via") for e in events if e.get("type") == "paid"],
        "refunds": [pick(e, "order_id", "refund_id", "status", "amount", "sku", "qty", "reconciliation_id",
                         "card_last4", "label") for e in events if e.get("type") == "refund_result"],
    }


def _mono(value) -> str:
    return f'<span class="mono">{escape(str(value))}</span>'


def _record_section(record: dict, session_id: str) -> str:
    """The dispute-ready record in plain words, with the download. Every value is escaped where it is built."""
    mandate = record.get("mandate") or {}
    signed = [s for s in record.get("signatures") or [] if s.get("type") == "signature_verified"]
    decision_words = {"allow": "allowed", "approve": "asked Priyank", "deny": "refused"}
    agreed = next((c for c in record.get("cosign") or []), None)
    turns = len(record.get("shopper_words") or [])
    rows = [
        ("Session", _mono(session_id)),
        ("Ruth's words", f'{turns} turn{"" if turns == 1 else "s"}, word for word'),
        ("Rules", (f'{_mono(mandate.get("mandate_id", ""))}<br>'
                   + ("Priyank's rules, signed by passkey " + escape(str(mandate.get("credential_id") or "")[:16])
                      if mandate.get("signed") else "Not signed (demo setting)")
                   + (f'. Ruth agreed {escape(METHOD_WORDS.get(str(agreed.get("method") or "voice")) or "by " + _words(agreed.get("method")))}.'
                      if agreed else "")
                   + (f'<br><span class="mono-s">sha-256 {escape(str(mandate.get("hash_b64url") or ""))}</span>'
                      if mandate.get("hash_b64url") else "")) if mandate else "Not available right now"),
        ("Decisions", "<br>".join(
            f'{_mono(d.get("decision_id"))} {escape(decision_words.get(str(d.get("decision", "")).lower()) or _words(d.get("decision")))}'
            for d in record.get("decisions") or []) or "None"),
        ("Signatures", "<br>".join(
            f'{sum(1 for c in s.get("checks") or [] if c.get("passed"))} of {len(s.get("checks") or [])} checks passed'
            f'<br><span class="mono-s">key {escape(str(s.get("keyid", "")))} · nonce {escape(str(s.get("nonce", ""))[:12])}…</span>'
            for s in signed) or "None"),
        ("Payments", "<br>".join(f'{escape(_money(p.get("total")))} · {_mono(p.get("order_id"))}'
                                 for p in record.get("payments") or []) or "None"),
        ("Refunds", "<br>".join(
            f'{escape(_money(r.get("amount")))} {escape(REFUND_WORDS.get(str(r.get("status") or "").upper()) or _words(r.get("status")))}'
            for r in record.get("refunds") or []) or "None"),
        ("Scam checks", "<br>".join(
            escape(_cap(": ".join(p for p in (VERDICT_WORDS.get(str(c.get("verdict") or "")) or _words(c.get("verdict")),
                                              _pattern(c.get("pattern"))) if p)))
            for c in record.get("scam_checks") or []) or "None"),
        ("Card swipes", "<br>".join(
            escape(f'{"Approved" if c.get("result") == "approved" else "Declined"} {_money(c.get("amount"))}'
                   f'{" at " + str(c.get("store")) if c.get("store") else ""}')
            for c in record.get("card_decisions") or [] if c.get("type") == "card_decision") or "None"),
        ("Visa risk", "<br>".join(
            escape(_join(r.get("store") or r.get("merchant"),
                         RISK_WORDS.get(str(r.get("status") or "").upper()) or _words(r.get("status")),
                         f'score {r.get("score")}' if r.get("score") not in (None, "") else ""))
            for r in record.get("risk_scores") or []) or "None"),
    ]
    body = "".join(f'<dt>{k}</dt><dd>{v}</dd>' for k, v in rows)
    return (f'<section class="card" aria-labelledby="h-record"><h2 id="h-record">Dispute-ready record</h2>'
            f'<p class="sub">What a bank needs to settle a dispute quickly, from Chaperone\'s ledger.</p>'
            f'<dl class="kv">{body}</dl>'
            f'<a class="ch-btn ch-btn--primary dl" href="{_attr(session_id)}/record.json" download>'
            f'{_icon("download", 18, 2.2)}Download the record (JSON)</a></section>')


def _intro(session_id: str, events: list[dict]) -> str:
    moments = [m for m in (_at_ms(_event_ms(e)) for e in events) if m]
    when = ""
    if moments:
        first, last = min(moments), max(moments)
        if first.date() != last.date():
            when = f"{_day(first)}, {_hm(first)} to {_day(last)}, {_hm(last)}"
        elif _hm(first) == _hm(last):
            when = f"{_day(first)} · {_hm(first)}"
        elif first.strftime("%p") == last.strftime("%p"):
            when = f"{_day(first)} · {_hm(first)[:-3]} to {_hm(last)}"
        else:
            when = f"{_day(first)} · {_hm(first)} to {_hm(last)}"
    langs = [LANG_NAMES[str(e.get("lang"))] for e in events if e.get("type") == "heard" and str(e.get("lang")) in LANG_NAMES]
    langs = list(dict.fromkeys(langs))
    spoken = f"in {' and '.join(langs)}" if langs else ""
    line = _join(when, spoken) or "Nothing recorded yet"
    return (f'<div class="intro"><h1>Ruth\'s session</h1><p class="when">{escape(line)}</p>'
            f'<p class="mono-s">session {escape(session_id)}</p></div>')


STYLE = """
body{margin:0;background:var(--ch-ground);color:var(--ch-text);font:400 16px/1.5 var(--ch-font);-webkit-text-size-adjust:100%;text-size-adjust:100%}
*,*::before,*::after{box-sizing:border-box}
.top{background:var(--ch-surface);border-bottom:1px solid var(--ch-line)}
.top-in{max-width:42rem;margin:0 auto;min-height:60px;display:flex;align-items:center;gap:10px;padding:10px 20px}
.top img{display:block;height:26px;width:auto;flex-shrink:0}
.sandbox{margin-left:auto;padding:4px 8px;border-radius:var(--ch-r-badge);background:var(--ch-raised);color:#333;font-size:12px;font-weight:600;line-height:1.35;text-align:center}
main{max-width:42rem;margin:0 auto;padding:14px 16px 32px;display:grid;gap:14px}
main>*{min-width:0}
.intro{padding:6px 4px 0;display:grid;gap:4px}
h1{margin:0;font-size:30px;line-height:1.25;font-weight:600}
.when{margin:0;font-size:15px;color:var(--ch-text-2)}
.card{background:var(--ch-surface);border-radius:var(--ch-r-panel);padding:18px;box-shadow:var(--ch-shadow-sm);min-width:0}
h2{margin:0 0 12px;font-size:18px;line-height:1.35;font-weight:600}
.sub{margin:-8px 0 12px;font-size:14px;color:var(--ch-text-2)}
.sec-h{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px 12px;margin-bottom:12px}
.sec-h h2{margin:0}
.empty{margin:0;font-size:15px;color:var(--ch-text-2)}
.mono,.mono-s{font-family:var(--ch-mono);overflow-wrap:anywhere;word-break:break-word}
.mono{font-size:13px}
.mono-s{font-size:12px;line-height:1.5;color:var(--ch-text-2)}
.ic{flex-shrink:0}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.st{display:flex;align-items:center;gap:12px;min-width:0}
.st+.st{margin-top:12px}
.st-main{flex:1;min-width:0}
.st-what{font-size:17px;font-weight:600;line-height:1.3}
.st-note{font-size:14px;line-height:1.45;margin-top:2px}
.st-amt{font-size:17px;font-weight:700;white-space:nowrap}
.circ{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.circ-ok{background:var(--ch-ok-bg);color:var(--ch-ok-text)}
.circ-wait{background:var(--ch-wait-bg);color:var(--ch-wait-strong)}
.circ-plain{background:var(--ch-raised);color:var(--ch-text-2)}
.circ-prot{background:var(--ch-surface);color:var(--ch-blue)}
.prot{background:var(--ch-blue);color:var(--ch-on-blue);box-shadow:var(--ch-shadow-md)}
.prot-h{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:700;letter-spacing:.02em}
.prot .st{align-items:flex-start}
.prot .st-note{color:var(--ch-on-blue)}
.prot .ch-badge--money{margin-top:6px}
.tl{list-style:none;margin:0;padding:0}
.tl li{display:grid;grid-template-columns:64px 12px minmax(0,1fr);column-gap:10px}
.tl-t{font:500 13px/1.5 var(--ch-mono);color:var(--ch-text-2);padding-top:1px;white-space:nowrap}
.tl-rail{display:flex;flex-direction:column;align-items:center}
.tl-rail::after{content:"";width:2px;flex-grow:1;background:var(--ch-line)}
.tl li:last-child .tl-rail::after{display:none}
.tl-dot{width:12px;height:12px;border-radius:50%;margin-top:5px;flex-shrink:0}
.tl-body{min-width:0;padding-bottom:16px}
.tl li:last-child .tl-body{padding-bottom:0}
.tl-h{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
.tl-title{font-size:15px;font-weight:600;line-height:1.4;min-width:0}
.tl-d{margin:0;font-size:14px;line-height:1.5;color:var(--ch-text-2);overflow-wrap:anywhere}
.turns{list-style:none;margin:0;padding:0;display:grid;gap:12px}
.turns li{min-width:0}
.who{display:block;font-size:13px;font-weight:600;color:var(--ch-text-2)}
.said{display:block;font-size:16px;line-height:1.5;overflow-wrap:anywhere}
.agent .said{color:var(--ch-text-2)}
.order-h{margin:0 0 2px}
.steps{list-style:none;margin:12px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
.steps .ch-badge{font-weight:600;white-space:normal}
.step-t{font-weight:500}
.lines{list-style:none;margin:12px 0 0;padding:10px 0 0;border-top:1px solid var(--ch-raised);display:grid;gap:6px;font-size:15px}
.lines li{display:flex;justify-content:space-between;gap:12px;min-width:0}
.lines li>span:first-child{min-width:0;overflow-wrap:anywhere}
.lines li>span:last-child{white-space:nowrap}
.lines .total{font-weight:700;padding-top:6px;border-top:1px solid var(--ch-raised)}
.order .tl-d{margin-top:8px}
.refunds{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:8px}
.refund{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-radius:var(--ch-r-row);background:var(--ch-ok-bg);color:var(--ch-ok-text)}
.refund .ic{margin-top:3px}
.refund>div{min-width:0}
.refund-h{font-size:15px;font-weight:600;overflow-wrap:anywhere}
.refund .tl-d{margin:0;color:var(--ch-ok-text)}
.proof{list-style:none;margin:0 0 12px;padding:0;border:1px solid var(--ch-line);border-radius:var(--ch-r-row)}
.pf{display:flex;gap:10px;padding:10px 12px;border-bottom:1px solid var(--ch-raised)}
.pf:last-child{border-bottom:0}
.pf .ic{margin-top:2px}
.pf-ok .ic{color:var(--ch-ok)}
.pf-no .ic{color:var(--ch-blue)}
.pf-unk .ic{color:var(--ch-field)}
.pf-b{min-width:0}
.pf-n{font-size:15px;font-weight:600}
.swipes{list-style:none;margin:0;padding:0;display:grid;gap:14px}
.swipes li{display:grid;grid-template-columns:64px minmax(0,1fr);column-gap:10px}
.sw-b{min-width:0;display:grid;gap:2px}
.kv{display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px 12px;margin:0 0 16px;font-size:14px}
.kv dt{color:var(--ch-text-2)}
.kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
.dl{width:100%;min-height:52px;border-radius:var(--ch-r-row)}
.foot{margin:4px 4px 0;font-size:13px;line-height:1.5;color:var(--ch-text-2)}
@media (min-width:720px){main{padding-top:20px}.kv{grid-template-columns:140px minmax(0,1fr)}}
"""


def render(session_id: str, events: list[dict], receipts: list[dict] | dict | None = None,
           orders_: list[dict] | None = None, record: dict | None = None, card_events: list[dict] | None = None) -> str:
    if isinstance(receipts, dict):
        receipts = [receipts]
    receipts = [r for r in receipts or [] if r]
    by_order = {str(r.get("order_id")): r for r in receipts if r.get("order_id")}
    carded = [o for o in reversed(orders_ or [])]  # the merchant lists newest first; the page reads oldest first
    card_ids = {str(o.get("order_id")) for o in carded}
    orders_html = "".join(_order_card(o, by_order.get(str(o.get("order_id")))) for o in carded)
    receipt_html = "".join(_receipt(r) for r in receipts if str(r.get("order_id")) not in card_ids)
    record_html = _record_section(record, session_id) if record else ""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_S}">
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/design/tokens.css">
<link rel="icon" href="/design/favicon.svg" type="image/svg+xml">
<title>Ruth's session · Chaperone</title><style>{STYLE}</style></head><body>
<header class="top"><div class="top-in"><img src="/design/logo.svg" alt="Chaperone" width="134" height="26">
<span class="sandbox">Visa sandbox · no real money</span></div></header>
<main>
{_intro(session_id, events)}
{_status(events)}
{_said(events)}
{_happened(events)}
{orders_html}{_proof(events)}{_swipes(card_events)}{receipt_html}{record_html}
<p class="foot">Read-only record from Chaperone's ledger. Refreshes every {REFRESH_S} seconds. Payments run in the Visa
sandbox with test cards; no real money moves. Pickup codes are never shown here.</p>
</main></body></html>"""
