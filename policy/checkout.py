"""Checkout orchestration. The engine stays pure; this module does the I/O."""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import requests

from common.config import KEY_ID, merchant_public_url
from policy.approvals import code_mac, new_nonce, state_of, remember_host_code
from policy.engine import dollars, evaluate, mandate_category, to_cents
from policy.events import post_event
from policy.mandate import DEFAULT_MANDATE
from policy.pricing import UnknownSku, reprice
from policy.store import add_spent_cents, get_decision, load_decisions, load_mandate, load_spent_cents, save_decision
from signer.sign import sign_request

JUDGE_THRESHOLD = float(os.environ.get("JUDGE_THRESHOLD", "0.6"))


class JudgeUnavailable(RuntimeError):
    pass


def _fake_judge() -> dict:
    return {"scam_score": 0.05, "patterns": ["none"], "rationale": "fake", "action": "proceed", "threshold": JUDGE_THRESHOLD}


MANDATE_SUMMARY_KEYS = ("currency", "per_purchase_cap", "monthly_cap", "approval_threshold", "allowed_categories", "blocked_categories")


def call_judge(transcript: str, cart: dict, mandate: dict, session_id: str | None = None) -> tuple[dict | None, str | None]:
    if os.environ.get("JUDGE_FAKE") == "1":
        return _fake_judge(), None
    try:
        from policy.judge import judge
    except ImportError:
        return None, "judge unavailable"
    try:
        # The judge takes the cart lines and the limits only; the passkey material never leaves this service.
        summary = {key: mandate[key] for key in MANDATE_SUMMARY_KEYS if key in mandate}
        result = judge(transcript, cart.get("items", []), summary, session_id=session_id)
        result["threshold"] = JUDGE_THRESHOLD
        return result, None
    except Exception as exc:  # noqa: BLE001 - R7 treats any judge failure as unavailable
        return None, str(exc)


def call_screen(transcript: str, lang: str, session_id: str | None = None) -> dict:
    try:
        from policy.screen import screen
    except ImportError:
        return {"action": "proceed", "hits": [], "refusal": None}
    # session_id lets the screen remember an earlier refusal in this session (repeat attempts go to the judge).
    return screen(transcript, lang or "en", session_id=session_id)


class UnsignedMandate(Exception):
    pass


class CartRejected(Exception):
    pass


class ReadBackRequired(Exception):
    pass


def active_mandate() -> tuple[dict, bool]:
    stored = load_mandate()
    if stored:
        return stored, False
    if os.environ.get("MANDATE_UNSIGNED_OK") == "1":
        return dict(DEFAULT_MANDATE), True
    raise UnsignedMandate()


def _order_body(mandate_id: str, decision_id: str, session_id: str, cart: dict, approval_id: str | None) -> dict:
    return {
        "mandate_id": mandate_id,
        "decision_id": decision_id,
        "session_id": session_id,
        "approval_id": approval_id,
        "cart": {
            "merchant": cart["merchant"],
            "total": cart["total"],
            "items": [
                {
                    "sku": item["sku"],
                    "name": item["name"],
                    "category": item["mandate_category"],
                    "qty": item["qty"],
                    "price": item["price"],
                }
                for item in cart["items"]
            ],
        },
    }


def send_signed_order(body: dict) -> dict:
    url = f"{merchant_public_url()}/orders"
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


def _check_cart(payload: dict, mandate: dict) -> None:
    if payload.get("read_back") is not True:
        raise ReadBackRequired()
    if payload.get("mandate_id") and payload["mandate_id"] != mandate["mandate_id"]:
        raise CartRejected("mandate_id does not match the active mandate")
    items = (payload.get("cart") or {}).get("items") or []
    if not items:
        raise CartRejected("cart is empty")
    for line in items:
        try:
            qty = int(line.get("qty"))
        except (TypeError, ValueError):
            raise CartRejected("qty must be from 1 to 24") from None
        if qty < 1 or qty > 24:
            raise CartRejected("qty must be from 1 to 24")


def checkout(payload: dict) -> dict:
    session_id = payload.get("session_id") or "none"
    transcript = payload.get("transcript") or ""
    lang = payload.get("lang") or "en"
    mandate, unsigned = active_mandate()
    _check_cart(payload, mandate)
    priced = reprice(payload.get("cart") or {})
    screen = call_screen(transcript, lang, session_id)
    blocked = set(mandate.get("blocked_categories") or [])
    category_blocked = any(mandate_category(item) in blocked for item in priced["items"])
    judgment, judge_error = (None, None)
    if screen.get("action") != "refuse" and not category_blocked:
        judgment, judge_error = call_judge(transcript, priced, mandate, session_id)
    decision = evaluate(
        priced,
        mandate,
        load_spent_cents(),
        judge=judgment,
        today=datetime.now(timezone.utc).date(),
        screen_action=screen.get("action"),
        judge_error=judge_error,
        signed=True if unsigned else None,
        unsigned_demo=unsigned,
    )
    if screen.get("action") == "refuse":
        decision["decision"] = "deny"
        spoken = (screen.get("refusal") or {}).get("spoken_key") or "blocked_category"
        decision["say_key"] = spoken
        rule_id = (screen.get("refusal") or {}).get("rule_id") or "screen"
        decision["rules"].append({"id": f"S_screen_{rule_id}", "passed": False, "detail": spoken})
        decision["monthly_total_after"] = dollars(load_spent_cents())
    decision_id = "d_" + uuid.uuid4().hex[:12]
    approval = None
    order = None
    order_error = None
    reused = False
    document = {
        **decision,
        "decision_id": decision_id,
        "mandate_id": mandate["mandate_id"],
        "session_id": session_id,
        "cart": priced,
        "order": None,
        "approval": None,
        "unsigned_mandate": unsigned,
    }
    if unsigned:
        document["detail"] = "unsigned mandate"
    if decision["decision"] == "allow":
        # The merchant looks this decision up before it accepts the order, so store it first.
        save_decision(document)
        body = _order_body(mandate["mandate_id"], decision_id, session_id, priced, None)
        try:
            merchant_order = send_signed_order(body)
            signature = merchant_order.pop("_signature", {})
            link = merchant_order.get("payment_link") or {}
            order = {
                "order_id": merchant_order.get("order_id"),
                "payment_link": link.get("url") or link,
                "status": merchant_order.get("status"),
            }
            post_event(
                "request_signed",
                session_id,
                mandate["mandate_id"],
                decision_id=decision_id,
                keyid=signature.get("keyid"),
                nonce=signature.get("nonce"),
                expires=signature.get("expires"),
            )
            add_spent_cents(to_cents(priced["total"]))
        except Exception as exc:  # noqa: BLE001 - the decision still stands if the merchant is down
            order_error = str(exc)
    elif decision["decision"] == "approve":
        existing = _open_approval(priced, mandate["mandate_id"])
        if existing:
            reused = True
            approval = existing["approval"]
            decision_id = existing["decision_id"]
            document = existing
        else:
            approval_id = "a_" + uuid.uuid4().hex[:12]
            expires = datetime.now(timezone.utc) + timedelta(seconds=90)
            code = f"{secrets.randbelow(1_000_000):06d}"
            approval = {
                "approval_id": approval_id,
                "session_id": session_id,
                "amount": priced["total"],
                "merchant": priced["merchant"],
                "excerpt": transcript[:240],
                "rule": "R6_approval_threshold",
                "expires_at": expires.isoformat(),
                "nonce": new_nonce(),
                "code_hash": code_mac(code, approval_id),
                "attempts": 0,
            }
            remember_host_code(approval_id, code, approval["expires_at"])
    else:
        failed = [rule["id"] for rule in decision["rules"] if not rule["passed"]]
        post_event(
            "refusal",
            session_id,
            mandate["mandate_id"],
            decision_id=decision_id,
            rule_id=failed[0] if failed else decision["say_key"],
            rule_ids=failed,
            spoken_key=decision["say_key"],
            lang=lang,
        )
        post_event("caregiver_alerted", session_id, mandate["mandate_id"], decision_id=decision_id)

    public_approval = None
    if approval:
        public_approval = {"approval_id": approval["approval_id"], "expires_at": approval["expires_at"]}
    document["order"] = order
    document["approval"] = approval
    save_decision(document)
    if approval and not reused:
        post_event(
            "approval_requested",
            session_id,
            mandate["mandate_id"],
            approval_id=approval["approval_id"],
            amount=approval["amount"],
            rule=approval["rule"],
            expires_at=approval["expires_at"],
        )
    failed = [rule["id"] for rule in decision["rules"] if not rule["passed"]]
    post_event(
        "policy_decision",
        session_id,
        mandate["mandate_id"],
        decision_id=decision_id,
        decision=decision["decision"],
        rules_failed=failed,
        total=priced["total"],
    )
    response = {
        "decision": decision["decision"],
        "rules": decision["rules"],
        "monthly_total_after": decision["monthly_total_after"],
        "decision_id": decision_id,
        "say_key": decision["say_key"],
        "order": order,
        "approval": public_approval,
        "judge": judgment,
    }
    if unsigned:
        response["detail"] = "unsigned mandate"
    if order_error:
        response["order_error"] = order_error
    if judge_error:
        response["judge_error"] = judge_error
    return response


def _cart_fingerprint(cart: dict) -> tuple:
    items = tuple(sorted(
        (str(item.get("sku")), int(item.get("qty") or 0), f"{float(item.get('price') or 0):.2f}")
        for item in (cart or {}).get("items") or []
    ))
    total = f"{float((cart or {}).get('total') or 0):.2f}"
    return ((cart or {}).get("merchant"), total, items)


def _open_approval(cart: dict, mandate_id: str) -> dict | None:
    for document in load_decisions().values():
        approval = document.get("approval")
        # allow and deny decisions share the store and carry no approval
        if not approval or document.get("mandate_id") != mandate_id or state_of(approval) != "pending":
            continue
        if _cart_fingerprint(document.get("cart") or {}) == _cart_fingerprint(cart):
            return document
    return None


def lookup(decision_id: str) -> dict | None:
    return get_decision(decision_id)
