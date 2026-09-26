"""Checkout orchestration. The engine stays pure; this module does the I/O."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import requests

from common.config import merchant_public_url
from policy.engine import dollars, evaluate, to_cents
from policy.events import post_event
from policy.mandate import DEFAULT_MANDATE
from policy.pricing import UnknownSku, reprice
from policy.store import get_decision, load_mandate, load_spent_cents, save_decision, store_spent_cents
from signer.sign import sign_request

JUDGE_THRESHOLD = float(os.environ.get("JUDGE_THRESHOLD", "0.6"))


class JudgeUnavailable(RuntimeError):
    pass


def _fake_judge() -> dict:
    return {"scam_score": 0.05, "patterns": ["none"], "rationale": "fake", "action": "proceed", "threshold": JUDGE_THRESHOLD}


def call_judge(transcript: str, cart: dict, mandate: dict) -> tuple[dict | None, str | None]:
    if os.environ.get("JUDGE_FAKE") == "1":
        return _fake_judge(), None
    try:
        from policy.judge import judge
    except ImportError:
        return None, "judge unavailable"
    try:
        result = judge(transcript, cart, mandate)
        result["threshold"] = JUDGE_THRESHOLD
        return result, None
    except Exception as exc:  # noqa: BLE001 - R7 treats any judge failure as unavailable
        return None, str(exc)


def call_screen(transcript: str, lang: str) -> dict:
    try:
        from policy.screen import screen
    except ImportError:
        return {"action": "proceed", "hits": [], "refusal": None}
    return screen(transcript, lang or "en")


class UnsignedMandate(Exception):
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
    prepared = sign_request(url, body)
    response = requests.Session().send(prepared, timeout=5)
    payload = response.json()
    if response.status_code >= 400:
        raise RuntimeError(payload.get("error") if isinstance(payload, dict) else response.text)
    return payload


def checkout(payload: dict) -> dict:
    session_id = payload.get("session_id") or "none"
    transcript = payload.get("transcript") or ""
    lang = payload.get("lang") or "en"
    mandate, unsigned = active_mandate()
    if payload.get("mandate_id"):
        mandate = {**mandate, "mandate_id": payload["mandate_id"]}
    priced = reprice(payload.get("cart") or {})
    screen = call_screen(transcript, lang)
    judgment, judge_error = (None, None)
    if screen.get("action") != "refuse":
        judgment, judge_error = call_judge(transcript, priced, mandate)
    decision = evaluate(
        priced,
        mandate,
        load_spent_cents(),
        judge=judgment,
        screen_action=screen.get("action"),
        judge_error=judge_error,
        signed=True if unsigned else None,
    )
    if screen.get("action") == "refuse":
        decision["decision"] = "deny"
        decision["say_key"] = (screen.get("refusal") or {}).get("spoken_key") or "blocked_category"
        decision["monthly_total_after"] = dollars(load_spent_cents())
    decision_id = "d_" + uuid.uuid4().hex[:12]
    approval = None
    order = None
    order_error = None
    if decision["decision"] == "allow":
        body = _order_body(mandate["mandate_id"], decision_id, session_id, priced, None)
        try:
            merchant_order = send_signed_order(body)
            link = merchant_order.get("payment_link") or {}
            order = {
                "order_id": merchant_order.get("order_id"),
                "payment_link": link.get("url") or link,
                "status": merchant_order.get("status"),
            }
            post_event("request_signed", session_id, mandate["mandate_id"], decision_id=decision_id)
            store_spent_cents(to_cents(decision["monthly_total_after"]))
        except Exception as exc:  # noqa: BLE001 - the decision still stands if the merchant is down
            order_error = str(exc)
    elif decision["decision"] == "approve":
        approval_id = "a_" + uuid.uuid4().hex[:12]
        nonce = secrets.token_hex(8)
        expires = datetime.now(timezone.utc) + timedelta(seconds=90)
        code = f"{secrets.randbelow(1_000_000):06d}"
        approval = {
            "approval_id": approval_id,
            "session_id": session_id,
            "amount": priced["total"],
            "excerpt": transcript[:240],
            "rule": "R6_approval_threshold",
            "expires_at": expires.isoformat(),
            "nonce": nonce,
            "code_hash": hashlib.sha256(f"{code}{approval_id}".encode()).hexdigest(),
            "attempts": 0,
        }
        print(f"approval code for {approval_id}: {code}")
        post_event("approval_requested", session_id, mandate["mandate_id"], approval_id=approval_id)
    else:
        post_event("refusal", session_id, mandate["mandate_id"], decision_id=decision_id, say_key=decision["say_key"])
        post_event("caregiver_alerted", session_id, mandate["mandate_id"], decision_id=decision_id)

    public_approval = None
    if approval:
        public_approval = {"approval_id": approval["approval_id"], "expires_at": approval["expires_at"]}
    document = {
        **decision,
        "decision_id": decision_id,
        "mandate_id": mandate["mandate_id"],
        "session_id": session_id,
        "cart": priced,
        "order": order,
        "approval": approval,
        "unsigned_mandate": unsigned,
    }
    if unsigned:
        document["detail"] = "unsigned mandate"
    save_decision(document)
    post_event("policy_decision", session_id, mandate["mandate_id"], decision_id=decision_id, decision=decision["decision"])
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


def lookup(decision_id: str) -> dict | None:
    return get_decision(decision_id)
