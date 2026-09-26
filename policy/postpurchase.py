"""Cancel, refund, history, pause and explain. The model never chooses an amount."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import requests

from common.config import KEY_ID, merchant_public_url
from policy.engine import dollars, to_cents
from policy.events import post_event
from policy.store import add_spent_cents, load_decisions, load_mandate, save_decision, save_mandate
from signer.sign import sign_request

PAID_OR_LATER = {"paid", "preparing", "ready_for_pickup", "picked_up", "partially_refunded"}
_EXPLAIN: dict[str, dict] = {}


def find_order(order_id: str) -> dict | None:
    for document in load_decisions().values():
        order = document.get("order") or {}
        if order.get("order_id") == order_id:
            return document
    return None


def signed_post(path: str, body: dict) -> dict:
    url = merchant_public_url().rstrip("/") + path
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=8)
    nonce = secrets.token_urlsafe(32)
    prepared = sign_request(url, body, created=now, expires=expires, nonce=nonce)
    response = requests.Session().send(prepared, timeout=5)
    if response.status_code >= 400:
        raise RuntimeError(f"merchant answered {response.status_code}: {response.text[:300]}")
    payload = response.json()
    payload["_signature"] = {"keyid": KEY_ID, "nonce": nonce, "expires": int(expires.timestamp())}
    return payload


def _rule(rule_id: str, passed: bool, detail: str) -> dict:
    return {"id": rule_id, "passed": passed, "detail": detail}


def cancel_order(order_id: str, mandate_id: str) -> dict:
    document = find_order(order_id)
    order = (document or {}).get("order") or {}
    if not document or document.get("mandate_id") != mandate_id:
        raise PermissionError("order is not on this mandate")
    if order.get("status") != "awaiting_payment":
        raise ValueError("only an unpaid order can be cancelled")
    order["cancel_requested"] = True
    save_decision(document)
    result = signed_post(f"/orders/{order_id}/cancel", {"order_id": order_id})
    order["status"] = "cancelled"
    order["link_status"] = result.get("link_status") or result.get("status")
    add_spent_cents(-to_cents(document["cart"]["total"]))
    save_decision(document)
    post_event(
        "order_cancelled",
        document.get("session_id") or "none",
        mandate_id,
        order_id=order_id,
        status="cancelled",
    )
    return {"order_id": order_id, "status": "cancelled", "link_status": order.get("link_status")}


def _line_refund_cents(document: dict, sku: str | None, qty: int | None) -> tuple[int, str]:
    items = (document.get("cart") or {}).get("items") or []
    if not sku:
        return sum(to_cents(item["price"]) * int(item["qty"]) for item in items), "full order"
    line = next((item for item in items if item.get("sku") == sku), None)
    if not line:
        return 0, "unknown sku"
    count = min(int(qty or line["qty"]), int(line["qty"]))
    return to_cents(line["price"]) * count, line.get("name") or sku


def _refunded_cents(document: dict) -> int:
    return sum(int(item.get("amount_cents") or 0) for item in document.get("refunds") or [])


def refund(payload: dict, today: datetime | None = None) -> dict:
    today = today or datetime.now(timezone.utc)
    mandate_id = payload.get("mandate_id") or ""
    document = find_order(str(payload.get("order_id") or ""))
    order = (document or {}).get("order") or {}
    owned = bool(document) and document.get("mandate_id") == mandate_id and order.get("status") in PAID_OR_LATER
    sku = payload.get("sku") or None
    qty = payload.get("qty")
    amount_cents, label = _line_refund_cents(document, sku, qty) if document else (0, "")
    already = _refunded_cents(document) if document else 0
    paid_cents = to_cents((document or {}).get("cart", {}).get("total") or 0) if document else 0
    within = owned and amount_cents > 0 and already + amount_cents <= paid_cents
    items = ((document or {}).get("cart") or {}).get("items") or []
    line = next((item for item in items if item.get("sku") == sku), None) if sku else None
    rx = (line or {}).get("category") == "pharmacy_pickup" or (line or {}).get("mandate_category") == "pharmacy_pickup"
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
    rules = [
        _rule("RF1_order_owned", owned, order.get("status") or "missing order"),
        _rule("RF2_amount_remaining", within, f"{dollars(amount_cents):.2f} of {dollars(max(paid_cents - already, 0)):.2f} left"),
        _rule("RF3_original_card", True, "refunds only return to the card that paid"),
        _rule("RF4_return_window", returnable, "pharmacy pickup" if rx else "30 days"),
        _rule("RF5_screen", not scam, "refused" if scam else "clear"),
        _rule("RF6_caregiver_told", True, "Priyank is told"),
    ]
    session_id = (document or {}).get("session_id") or payload.get("session_id") or "none"
    if scam:
        post_event("caregiver_alerted", session_id, mandate_id, kind="refund", decision_id=(document or {}).get("decision_id"))
    say = "refund_scam" if scam else ("refund_not_allowed_rx" if rx else "refund_preview")
    preview = {"amount": dollars(amount_cents), "card_last4": order.get("card_last4") or "sandbox", "items": label}
    failed = [rule for rule in rules if not rule["passed"]]
    if not payload.get("confirmed"):
        if document and not failed:
            document["refund_preview"] = {"sku": sku, "qty": qty, "amount_cents": amount_cents}
            save_decision(document)
        return {"preview": preview, "say": say if failed or not payload.get("confirmed") else "refund_preview", "rules": rules, "ok": not failed}
    if failed or not document:
        return {"ok": False, "say": say, "rules": rules, "preview": preview}
    shown = document.get("refund_preview") or {}
    if shown.get("sku") != sku or shown.get("amount_cents") != amount_cents:
        return {"ok": False, "say": "refund_preview", "rules": rules, "preview": preview, "detail": "read the preview back first"}
    result = signed_post(f"/orders/{order['order_id']}/refunds", {"sku": sku, "qty": qty, "amount": dollars(amount_cents)})
    if result.get("status") != "PENDING":
        raise RuntimeError("merchant did not accept the refund")
    add_spent_cents(-amount_cents)
    document.setdefault("refunds", []).append({"sku": sku, "qty": qty, "amount_cents": amount_cents, "id": result.get("id")})
    document.pop("refund_preview", None)
    order["status"] = "refunded" if already + amount_cents >= paid_cents else "partially_refunded"
    save_decision(document)
    post_event("refund_requested", session_id, mandate_id, order_id=order["order_id"], amount=dollars(amount_cents))
    post_event("refund_result", session_id, mandate_id, order_id=order["order_id"], status=result.get("status"))
    post_event("caregiver_alerted", session_id, mandate_id, kind="refund", order_id=order["order_id"])
    return {"ok": True, "say": "refund_done", "rules": rules, "refund": result, "preview": preview}


def history(mandate_id: str, days: int = 30) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 60)))
    orders, refunds, refusals = [], [], []
    for document in load_decisions().values():
        if mandate_id and document.get("mandate_id") != mandate_id:
            continue
        created = document.get("created_at")
        if created and datetime.fromisoformat(created) < cutoff:
            continue
        if document.get("order"):
            orders.append({"order_id": document["order"].get("order_id"), "status": document["order"].get("status"), "total": (document.get("cart") or {}).get("total"), "decision_id": document.get("decision_id")})
        for item in document.get("refunds") or []:
            refunds.append({"order_id": (document.get("order") or {}).get("order_id"), "amount": dollars(item.get("amount_cents") or 0), "sku": item.get("sku")})
        if document.get("decision") == "deny":
            refusals.append({"decision_id": document.get("decision_id"), "say_key": document.get("say_key"), "total": (document.get("cart") or {}).get("total")})
    return {"orders": orders, "refunds": refunds, "refusals": refusals, "days": days}


def pause_mandate() -> dict:
    stored = load_mandate()
    if not stored:
        raise LookupError("no mandate")
    stored["paused"] = True
    save_mandate(stored)
    post_event("mandate_paused", "none", stored.get("mandate_id") or "none")
    return {"paused": True, "mandate_id": stored.get("mandate_id")}


def resume_challenge(nonce: str, expires_at: str, mandate_id: str) -> bytes:
    import hashlib

    import jcs

    return hashlib.sha256(jcs.canonicalize({"action": "resume", "mandate_id": mandate_id, "nonce": nonce, "expires_at": expires_at})).digest()


def explain_decision(decision_id: str) -> dict:
    if decision_id in _EXPLAIN:
        return _EXPLAIN[decision_id]
    document = load_decisions().get(decision_id)
    if not document:
        raise LookupError("unknown decision")
    failed = next((rule for rule in document.get("rules") or [] if not rule.get("passed")), None)
    fallback = {
        "headline": "Ruth was stopped",
        "what_happened": document.get("say_key") or "declined",
        "rule_in_plain_words": f"{failed['id']}: {failed.get('detail')}" if failed else "Every rule passed",
        "what_ruth_heard": document.get("say_key") or "",
        "what_you_can_do": "Change the rules, or call Ruth.",
    }
    if os.environ.get("EXPLAIN_FAKE") == "1":
        answer = {**fallback, "headline": "Explained without calling the model"}
    else:
        answer = _explain_live(document) or fallback
    _EXPLAIN[decision_id] = answer
    post_event("explanation_requested", document.get("session_id") or "none", document.get("mandate_id") or "none", decision_id=decision_id)
    return answer


def _explain_live(document: dict) -> dict | None:
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout

    from common.config import env

    key = env("XAI_API_KEY")
    if not key:
        return None
    failed = [rule for rule in document.get("rules") or [] if not rule.get("passed")]
    prompt = (
        "Explain this shopping refusal to the caregiver in short plain sentences. "
        "Reply with JSON keys headline, what_happened, rule_in_plain_words, what_ruth_heard, what_you_can_do. "
        f"Rules: {failed}. Say key: {document.get('say_key')}."
    )

    def call() -> dict:
        from openai import OpenAI

        client = OpenAI(base_url="https://api.x.ai/v1", api_key=key, max_retries=0)
        reply = client.chat.completions.create(
            model="grok-4.20-0309-non-reasoning",
            messages=[{"role": "user", "content": prompt}],
            timeout=3,
        )
        import json

        return json.loads(reply.choices[0].message.content)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(call).result(timeout=3)
    except (FutureTimeout, Exception):
        return None
