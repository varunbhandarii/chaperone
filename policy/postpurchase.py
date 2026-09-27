"""Cancel, refund, history, pause and explain. The model never chooses an amount."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import requests

from common.config import KEY_ID, merchant_public_url
from policy.engine import dollars, to_cents
from policy.events import post_event
from policy.mandate import DEFAULT_MANDATE
from policy.store import add_spent_cents, load_decisions, load_mandate, load_paused, save_decision, save_paused
from signer.sign import sign_request

PAID_OR_LATER = {"paid", "preparing", "ready_for_pickup", "picked_up", "partially_refunded"}
_EXPLAIN: dict[str, dict] = {}


def orders_of(document: dict) -> list[dict]:
    """Every order placed under a decision: one per store when the cart spanned stores."""
    listed = [o for o in document.get("orders") or [] if isinstance(o, dict) and o.get("order_id")]
    if listed:
        return listed
    order = document.get("order")
    return [order] if isinstance(order, dict) and order.get("order_id") else []


def order_entry(document: dict, order_id: str | None = None) -> dict:
    found = orders_of(document)
    if order_id:
        return next((o for o in found if o.get("order_id") == order_id), {})
    return found[0] if found else {}


def _mirror(document: dict, entry: dict) -> None:
    """document["order"] is the first store's order; keep it in step when that order changes."""
    primary = document.get("order")
    if isinstance(primary, dict) and entry and primary is not entry and primary.get("order_id") == entry.get("order_id"):
        primary.update(entry)


def _split(document: dict) -> bool:
    return len(orders_of(document)) > 1


def order_items(document: dict, entry: dict) -> list[dict]:
    """The cart lines of one order: its store's lines when the decision was split by store."""
    items = (document.get("cart") or {}).get("items") or []
    if not _split(document):
        return items
    store = entry.get("merchant")
    return [item for item in items if item.get("merchant") == store]


def order_total_cents(document: dict, entry: dict) -> int:
    if _split(document) and entry.get("total") is not None:
        return to_cents(entry["total"])
    return to_cents((document.get("cart") or {}).get("total") or 0)


def find_order(order_id: str) -> dict | None:
    for document in load_decisions().values():
        if any(order.get("order_id") == order_id for order in orders_of(document)):
            return document
    return None


def _merchant_orders(order_id: str | None = None) -> list[dict] | None:
    """The merchant's live orders. Policy's copy is written at checkout and never hears about payment, pickup or
    the merchant's own refunds, so status and paid_at are read from the merchant before any rule uses them."""
    path = f"/orders/{order_id}" if order_id else "/orders"
    try:
        response = requests.get(merchant_public_url().rstrip("/") + path, timeout=2)
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    found = response.json()
    return found if isinstance(found, list) else [found]


def _overlay(order: dict, live: dict) -> dict:
    order["status"] = live.get("status") or order.get("status")
    paid_at = live.get("paid_at")
    if isinstance(paid_at, (int, float)):
        order["paid_at"] = datetime.fromtimestamp(paid_at, timezone.utc).isoformat()
    if live.get("card_last4"):
        order["card_last4"] = live["card_last4"]
    return order


def sync_order(document: dict, order_id: str | None = None) -> dict:
    order = order_entry(document, order_id)
    live = _merchant_orders(order.get("order_id")) if order.get("order_id") else None
    if live:
        _overlay(order, live[0])
        _mirror(document, order)
        save_decision(document)
    return order


class MerchantRefused(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"merchant answered {status}: {text[:300]}")
        self.status = status


def signed_post(path: str, body: dict) -> dict:
    url = merchant_public_url().rstrip("/") + path
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=8)
    nonce = secrets.token_urlsafe(32)
    prepared = sign_request(url, body, created=now, expires=expires, nonce=nonce)
    try:
        # a cancel waits for Visa's PATCH and its read-back
        response = requests.Session().send(prepared, timeout=12)
    except requests.RequestException as exc:
        raise RuntimeError(f"merchant unreachable ({type(exc).__name__})") from exc
    if response.status_code >= 400:
        raise MerchantRefused(response.status_code, response.text)
    payload = response.json()
    payload["_signature"] = {"keyid": KEY_ID, "nonce": nonce, "expires": int(expires.timestamp())}
    return payload


def _rule(rule_id: str, passed: bool, detail: str) -> dict:
    return {"id": rule_id, "passed": passed, "detail": detail}


def cancel_order(order_id: str, mandate_id: str) -> dict:
    document = find_order(order_id)
    if not document or document.get("mandate_id") != mandate_id:
        raise PermissionError("order is not on this mandate")
    order = sync_order(document, order_id)
    if order.get("status") != "awaiting_payment":
        raise ValueError("only an unpaid order can be cancelled")
    order["cancel_requested"] = True
    save_decision(document)
    result = signed_post(
        f"/orders/{order_id}/cancel",
        {"order_id": order_id, "mandate_id": mandate_id, "decision_id": document["decision_id"], "session_id": document.get("session_id")},
    )
    order["status"] = "cancelled"
    order["link_status"] = result.get("link_status") or result.get("status")
    _mirror(document, order)
    add_spent_cents(-order_total_cents(document, order))  # only this store's share when the cart was split
    save_decision(document)
    return {"order_id": order_id, "status": "cancelled", "link_status": order.get("link_status")}


def _is_rx(line: dict) -> bool:
    return "pharmacy_pickup" in (line.get("category"), line.get("mandate_category"))


def _left_qty(document: dict, line: dict) -> int:
    returned = sum(int(item.get("qty") or 0) for item in document.get("refunds") or [] if item.get("sku") == line.get("sku"))
    return int(line.get("qty") or 0) - returned


def _refund_line(document: dict, sku: str | None, entry: dict | None = None) -> dict | None:
    """The named line, or when none is named, the only line that can still come back."""
    items = order_items(document, entry or order_entry(document))
    if sku:
        return next((item for item in items if item.get("sku") == sku), None)
    open_lines = [item for item in items if not _is_rx(item) and _left_qty(document, item) > 0]
    return open_lines[0] if len(open_lines) == 1 else None


def _refunded_cents(document: dict, entry: dict | None = None) -> int:
    order_id = (entry or {}).get("order_id")
    return sum(int(item.get("amount_cents") or 0) for item in document.get("refunds") or []
               if not _split(document) or item.get("order_id") in (None, order_id))


def refund(payload: dict, today: datetime | None = None) -> dict:
    """Replies as the station reads them: {preview} to read back, {refund} once the merchant took it, or a deny
    decision with a say_key. Only one of the three keys is ever present."""
    today = today or datetime.now(timezone.utc)
    mandate_id = payload.get("mandate_id") or ""
    document = find_order(str(payload.get("order_id") or ""))
    order = sync_order(document, str(payload.get("order_id") or "")) if document else {}
    owned = bool(document) and document.get("mandate_id") == mandate_id and order.get("status") in PAID_OR_LATER
    line = _refund_line(document, payload.get("sku") or None, order) if document else None
    sku = (line or {}).get("sku")
    left = _left_qty(document, line) if line else 0
    try:
        qty = int(payload.get("qty") or left)
    except (TypeError, ValueError):
        qty = 0
    amount_cents = to_cents(line["price"]) * qty if line and 0 < qty <= left else 0
    already = _refunded_cents(document, order) if document else 0
    paid_cents = order_total_cents(document, order) if document else 0
    within = owned and amount_cents > 0 and already + amount_cents <= paid_cents
    if not line:
        amount_detail = "say which item"
    elif not 0 < qty <= left:
        amount_detail = f"{max(left, 0)} of {line.get('name') or sku} left to return"
    else:
        amount_detail = f"{dollars(amount_cents):.2f} of {dollars(max(paid_cents - already, 0)):.2f} left"
    rx = bool(line) and _is_rx(line)
    paid_at = order.get("paid_at")
    window_ok = True
    if paid_at:
        opened = datetime.fromisoformat(paid_at)
        window_ok = (today - opened).days <= 30
    returnable = owned and not rx and window_ok
    from policy.screen import screen

    transcript = payload.get("transcript") or ""
    screened = screen(transcript, payload.get("lang") or "en", session_id=payload.get("session_id")) if transcript else {"action": "proceed", "hits": []}
    scam = screened.get("action") == "refuse"
    scam_key = (screened.get("refusal") or {}).get("spoken_key") or "refund_scam"
    rules = [
        _rule("RF1_order_owned", owned, order.get("status") or "missing order"),
        _rule("RF2_amount_remaining", within, amount_detail),
        _rule("RF3_original_card", True, "refunds only return to the card that paid"),
        _rule("RF4_return_window", returnable, "pharmacy pickup" if rx else "30 days"),
        _rule("RF5_screen", not scam, "refused" if scam else "clear"),
        _rule("RF6_caregiver_told", True, "Priyank is told"),
    ]
    session_id = (document or {}).get("session_id") or payload.get("session_id") or "none"
    if scam:
        post_event("caregiver_alerted", session_id, mandate_id, kind="refund", order_id=order.get("order_id"),
                   say_key=scam_key)
    say_key = scam_key if scam else ("refund_not_allowed_rx" if rx else "refund_not_possible")
    failed = [rule for rule in rules if not rule["passed"]]
    if failed or not document:
        return {"ok": False, "decision": "deny", "say_key": say_key, "rules": rules}
    if not payload.get("confirmed"):
        document["refund_preview"] = {"sku": sku, "qty": qty, "amount_cents": amount_cents}
        save_decision(document)
        preview = {
            "amount": dollars(amount_cents),
            "card_last4": order.get("card_last4") or "sandbox",
            "items": [{"name": line.get("name") or sku, "qty": qty, "amount": dollars(amount_cents)}],
        }
        return {"ok": True, "preview": preview, "say": "refund_preview", "rules": rules}
    shown = document.get("refund_preview") or {}
    if (shown.get("sku"), shown.get("qty"), shown.get("amount_cents")) != (sku, qty, amount_cents):
        return {"ok": False, "decision": "deny", "say_key": "refund_not_possible", "rules": rules, "detail": "read the preview back first"}
    result = signed_post(
        f"/orders/{order['order_id']}/refunds",
        {
            "order_id": order["order_id"],
            "mandate_id": mandate_id,
            "decision_id": document["decision_id"],
            "session_id": document.get("session_id"),
            "sku": sku,
            "qty": qty,
            "amount": f"{amount_cents / 100:.2f}",
            "reason": str(payload.get("reason") or "")[:200] or None,
        },
    )
    if result.get("status") != "PENDING":
        raise RuntimeError("merchant did not accept the refund")
    add_spent_cents(-amount_cents)
    document.setdefault("refunds", []).append({"sku": sku, "qty": qty, "amount_cents": amount_cents, "id": result.get("id"),
                                               "status": result.get("status"), "at": today.isoformat(),
                                               "order_id": order["order_id"]})
    document.pop("refund_preview", None)
    order["status"] = "refunded" if already + amount_cents >= paid_cents else "partially_refunded"
    _mirror(document, order)
    save_decision(document)
    post_event("refund_requested", session_id, mandate_id, order_id=order["order_id"], amount=dollars(amount_cents),
               sku=sku, qty=qty, rules=rules)
    post_event("caregiver_alerted", session_id, mandate_id, kind="refund", order_id=order["order_id"])
    return {"ok": True, "say_key": "refund_done", "rules": rules, "refund": result}


def history(mandate_id: str, days: int = 30) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 60)))
    orders, refunds, refusals = [], [], []
    live = {o.get("order_id"): o for o in _merchant_orders() or []}
    for document in load_decisions().values():
        if mandate_id and document.get("mandate_id") != mandate_id:
            continue
        created = document.get("created_at")
        if created and datetime.fromisoformat(created) < cutoff:
            continue
        cards = {}
        for entry in orders_of(document):  # one row per store's order
            if entry.get("order_id") in live:
                _overlay(entry, live[entry["order_id"]])
            cards[entry.get("order_id")] = entry.get("card_last4")
            orders.append({
                "order_id": entry.get("order_id"),
                "status": entry.get("status"),
                "total": dollars(order_total_cents(document, entry)),
                "store": entry.get("merchant") or (document.get("cart") or {}).get("merchant"),
                "decision_id": document.get("decision_id"),
                "at": created,
                "items": [f"{item.get('qty')} x {item.get('name')}" for item in order_items(document, entry)],
                "card_last4": entry.get("card_last4"),
            })
        for item in document.get("refunds") or []:
            refund_order = item.get("order_id") or order_entry(document).get("order_id")
            refunds.append({"order_id": refund_order, "amount": dollars(item.get("amount_cents") or 0),
                            "sku": item.get("sku"), "status": item.get("status") or "PENDING", "at": item.get("at"),
                            "card_last4": cards.get(refund_order)})
        if document.get("decision") == "deny":
            refusals.append({"decision_id": document.get("decision_id"), "say_key": document.get("say_key"), "total": (document.get("cart") or {}).get("total"), "at": created})
    orders.sort(key=lambda o: o.get("at") or "", reverse=True)
    totals = {"orders": round(sum(float(o.get("total") or 0) for o in orders if o.get("status") != "cancelled"), 2),
              "refunds": round(sum(r["amount"] for r in refunds), 2)}
    return {"orders": orders, "refunds": refunds, "refusals": refusals, "days": days, "totals": totals}


def pause_mandate() -> dict:
    mandate_id = (load_mandate() or DEFAULT_MANDATE).get("mandate_id") or "none"
    save_paused(True)
    post_event("mandate_paused", "none", mandate_id)
    return {"paused": True, "mandate_id": mandate_id}


def resume_challenge(nonce: str, expires_at: str, mandate_id: str) -> bytes:
    import hashlib

    import jcs

    return hashlib.sha256(jcs.canonicalize({"action": "resume", "mandate_id": mandate_id, "nonce": nonce, "expires_at": expires_at})).digest()


def _scam_check_fallback(document: dict) -> dict:
    check = document.get("scam_check") or {}
    amount = document.get("amount") or check.get("amount")
    asked = f" asking for ${float(amount):,.2f}" if amount else " asking for money"
    scam = check.get("verdict") == "scam"
    return {
        "headline": "Chaperone flagged a scam call Ruth described" if scam else "Chaperone checked a call Ruth described",
        "what_happened": f"Ruth described a call{asked}, and Chaperone checked it against her accounts and recent reports.",
        "rule_in_plain_words": ("It matches a scam people are reporting now, so her card takes extra care for a day."
                                if scam else "Chaperone could not be sure it was safe, so it asked Ruth to be careful."),
        "what_ruth_heard": "Chaperone told Ruth calmly not to pay and to hang up.",
        "what_you_can_do": "Call Ruth, and if a family member was named, check on them at the number you know.",
    }


def _fallback(document: dict) -> dict:
    """Plain words from the stored decision, for when the model is off or late. No rule ids."""
    from ai.explain import FAKE

    if document.get("source") == "scam_check":
        return _scam_check_fallback(document)

    names = ", ".join(str(item.get("name")) for item in (document.get("cart") or {}).get("items") or [])
    heard = _heard(document)
    return {
        **FAKE,
        "what_happened": f"Ruth asked for {names}, and nothing was bought." if names else FAKE["what_happened"],
        **({"what_ruth_heard": heard} if heard else {}),
    }


def _heard(document: dict) -> str:
    from ai.explain import _spoken_en

    return _spoken_en().get(document.get("say_key") or "", "")


def explain_decision(decision_id: str) -> dict:
    if decision_id in _EXPLAIN:
        return _EXPLAIN[decision_id]
    document = load_decisions().get(decision_id)
    if not document:
        raise LookupError("unknown decision")
    from ai.explain import ExplainError, explain

    mandate = load_mandate() or DEFAULT_MANDATE
    limits = {key: mandate.get(key) for key in ("per_purchase_cap", "monthly_cap", "approval_threshold", "blocked_categories")}
    try:
        answer = explain(document, ruth_said=document.get("ruth_said") or "", screen_hits=document.get("screen_hits"), limits=limits)
        _EXPLAIN[decision_id] = answer  # a model answer is kept; a fallback is not, so the next tap tries again
    except ExplainError:
        answer = _fallback(document)
    post_event("explanation_requested", document.get("session_id") or "none", document.get("mandate_id") or "none", decision_id=decision_id)
    return answer
