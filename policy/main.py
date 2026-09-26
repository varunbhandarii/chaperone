"""Policy service. python -m uvicorn policy.main:app --host 0.0.0.0 --port 8001"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from common import host_header

from policy.approvals import code_mac, find_approval, forget_host_code, host_code, marker_matches, public_approval, public_decision, state_of
from policy.checkout import CartRejected, ReadBackRequired, UnsignedMandate, checkout as run_checkout
from policy.checkout import send_signed_order
from policy.engine import dollars, to_cents
from policy.events import post_event
from policy.mandate import DEFAULT_MANDATE, fill_v2
from policy.pricing import UnknownSku
from policy.store import add_spent_cents, hold_decisions, load_caregiver_credential, load_mandate, load_paused, load_spent_cents, reset as reset_store, save_caregiver_credential, save_decision, save_mandate, save_paused
from policy.verify_mandate import relying_party, verify_mandate_assertion

app = FastAPI(title="Chaperone policy")

try:
    from policy.screen import router as screen_router

    app.include_router(screen_router)
except ImportError:
    pass

try:
    from policy.risk import router as risk_router

    app.include_router(risk_router)
except ImportError:
    pass


@app.on_event("startup")
def _require_code_key() -> None:
    if os.environ.get("POLICY_CODE_KEY") or os.environ.get("POLICY_DEV_KEY_OK") == "1":
        return
    raise RuntimeError("POLICY_CODE_KEY is required unless POLICY_DEV_KEY_OK=1")


@app.get("/health")
def health():
    return {"ok": True, "service": "policy"}


@app.get("/budget")
def budget(mandate_id: str = ""):
    del mandate_id
    stored = load_mandate() or DEFAULT_MANDATE
    spent = load_spent_cents()
    cap = int(round(float(stored["monthly_cap"]) * 100))
    return {"monthly_cap": stored["monthly_cap"], "spent": dollars(spent), "left": dollars(cap - spent)}


@app.post("/checkout")
def checkout(payload: dict):
    if not payload.get("session_id") or not payload.get("cart"):
        raise HTTPException(422, "session_id and cart are required")
    try:
        return run_checkout(payload)
    except UnknownSku as exc:
        raise HTTPException(422, f"unknown sku {exc.sku}") from exc
    except UnsignedMandate as exc:
        raise HTTPException(403, "mandate is not signed") from exc
    except CartRejected as exc:
        raise HTTPException(422, str(exc)) from exc
    except ReadBackRequired as exc:
        raise HTTPException(409, "read_back_required") from exc


@app.get("/decisions/{decision_id}")
def decision(decision_id: str):
    from policy.store import get_decision

    found = get_decision(decision_id)
    if not found:
        raise HTTPException(404, "unknown decision")
    return public_decision(found)


@app.post("/reset")
def reset(request: Request):
    host_header.require(request)  # only the relay's Host fan-out resets policy
    reset_store()
    forget_host_code()
    return {"ok": True, "spent": dollars(load_spent_cents())}


@app.post("/mandate")
def put_mandate(mandate: dict):
    try:
        verify_mandate_assertion(mandate)
    except Exception as exc:  # noqa: BLE001 - the phone needs the verifier's reason
        raise HTTPException(400, str(exc)) from exc
    save_mandate(mandate)
    post_event("mandate_signed", "none", mandate.get("mandate_id", "none"))
    return {"ok": True, "mandate_id": mandate.get("mandate_id")}


@app.get("/mandate")
def get_mandate():
    stored = load_mandate()
    paused = load_paused()
    if stored:
        public = fill_v2({key: value for key, value in stored.items() if key not in ("passkey", "paused")})
        credential_id = (stored.get("passkey") or {}).get("credential_id")
        return {"signed": True, "credential_id": credential_id, "mandate": public, "paused": paused}
    return {"signed": False, "mandate": DEFAULT_MANDATE, "detail": "unsigned mandate", "paused": paused}


def _finish_approval(document: dict, method: str) -> dict:
    approval = document["approval"]
    forget_host_code(approval["approval_id"])
    if state_of(approval) == "expired":
        post_event(
            "approval_result",
            document["session_id"],
            document["mandate_id"],
            approval_id=approval["approval_id"],
            approved=False,
            method="timeout",
        )
        save_decision(document)
        return public_approval(document)
    if load_paused():  # Priyank paused after he was asked: nothing is bought
        return _close_rejected(document, approval["approval_id"], "Shopping is paused")
    mandate = load_mandate() or DEFAULT_MANDATE
    body = {
        "mandate_id": document["mandate_id"],
        "decision_id": document["decision_id"],
        "session_id": document["session_id"],
        "approval_id": approval["approval_id"],
        "cart": {
            "merchant": document["cart"]["merchant"],
            "total": document["cart"]["total"],
            "items": [
                {
                    "sku": item["sku"],
                    "name": item["name"],
                    "category": item.get("mandate_category") or item.get("category"),
                    "qty": item["qty"],
                    "price": item["price"],
                }
                for item in document["cart"]["items"]
            ],
        },
    }
    save_decision(document)
    try:
        merchant_order = send_signed_order(body)
    except Exception as exc:  # noqa: BLE001 - the station speaks checkout_unavailable from order_error
        document["order_error"] = str(exc)
        approval["used"] = True
        save_decision(document)
        post_event(
            "approval_result",
            document["session_id"],
            document["mandate_id"],
            approval_id=approval["approval_id"],
            approved=True,
            method=method,
            error=document["order_error"],
        )
        view = public_approval(document)
        view["order_error"] = document["order_error"]
        return view
    signature = merchant_order.pop("_signature", {}) or {}
    post_event(
        "request_signed",
        document["session_id"],
        document["mandate_id"],
        decision_id=document["decision_id"],
        keyid=signature.get("keyid"),
        nonce=signature.get("nonce"),
        expires=signature.get("expires"),
    )
    add_spent_cents(to_cents(float(document["cart"]["total"])))
    link = merchant_order.get("payment_link") or {}
    document["order"] = {
        "order_id": merchant_order.get("order_id"),
        "payment_link": link.get("url") or link,
        "status": merchant_order.get("status"),
    }
    approval["used"] = True
    post_event(
        "approval_result",
        document["session_id"],
        mandate["mandate_id"],
        approval_id=approval["approval_id"],
        approved=True,
        method=method,
    )
    save_decision(document)
    return public_approval(document)


@app.get("/approvals")
def list_approvals(request: Request):
    from policy.store import load_decisions

    show_excerpt = marker_matches("list", request.headers.get("x-chaperone-marker", ""), "list")
    pending = []
    for document in load_decisions().values():
        if not document.get("approval"):
            continue
        view = public_approval(document)
        if view["state"] != "pending":
            continue
        if not show_excerpt:
            view.pop("excerpt", None)
        pending.append(view)
    return pending


@app.get("/approvals/{approval_id}/challenge")
def approval_challenge(approval_id: str):
    from policy.approvals import challenge_bytes
    from webauthn.helpers import bytes_to_base64url

    document = find_approval(approval_id)
    if not document:
        raise HTTPException(404, "unknown approval")
    return {"challenge": bytes_to_base64url(challenge_bytes(document["approval"]))}


@app.get("/approvals/{approval_id}")
def approval_status(approval_id: str):
    document = find_approval(approval_id)
    if not document:
        raise HTTPException(404, "unknown approval")
    view = public_approval(document)
    if view["state"] == "expired" and not document["approval"].get("timeout_posted"):
        document["approval"]["timeout_posted"] = True
        post_event(
            "approval_result",
            document["session_id"],
            document["mandate_id"],
            approval_id=approval_id,
            approved=False,
            method="timeout",
        )
        save_decision(document)
    return view


def _close_rejected(document: dict, approval_id: str, message: str) -> dict:
    approval = document["approval"]
    approval["approved"] = False
    approval["used"] = True
    if message:
        approval["message"] = message[:140]
    forget_host_code(approval_id)
    post_event(
        "approval_result",
        document["session_id"],
        document["mandate_id"],
        approval_id=approval_id,
        approved=False,
        method="passkey",
        **({"message": approval["message"]} if approval.get("message") else {}),
    )
    save_decision(document)
    return public_approval(document)


@app.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, payload: dict, request: Request):
    with hold_decisions():
        return _decide(approval_id, payload, request)


def _decide(approval_id: str, payload: dict, request: Request):
    document = find_approval(approval_id)
    if not document:
        raise HTTPException(404, "unknown approval")
    approval = document["approval"]
    if approval.get("used") or state_of(approval) != "pending":
        raise HTTPException(400, "approval is closed")
    if payload.get("approved") is False:
        message = str(payload.get("message") or "")
        if marker_matches(approval_id, request.headers.get("x-chaperone-marker", "")):
            return _close_rejected(document, approval_id, message)
        if not payload.get("response"):
            return JSONResponse({"error": "passkey assertion required"}, status_code=400)
        _verify_assertion(document, payload)
        return _close_rejected(document, approval_id, message)
    pinned = load_caregiver_credential()
    if not pinned:
        stored = load_mandate() or {}
        passkey = stored.get("passkey") or {}
        if passkey.get("credential_id") and passkey.get("public_key"):
            pinned = {"credential_id": passkey["credential_id"], "public_key": passkey["public_key"], "sign_count": 0}
            save_caregiver_credential(pinned)
    if (
        marker_matches(approval_id, request.headers.get("x-chaperone-marker", ""), "approve")
        and payload.get("response")
    ):
        if pinned and payload.get("sign_count") is not None:
            pinned["sign_count"] = int(payload["sign_count"])
            save_caregiver_credential(pinned)
        approval["approved"] = True
        return _finish_approval(document, "passkey")
    if not pinned or not payload.get("response"):
        return JSONResponse({"error": "passkey assertion required"}, status_code=400)
    _verify_assertion(document, payload)
    approval["approved"] = True
    return _finish_approval(document, "passkey")


def _verify_assertion(document: dict, payload: dict) -> None:
    from policy.approvals import challenge_bytes
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    pinned = load_caregiver_credential()
    if not pinned:
        raise HTTPException(400, "passkey assertion required")
    host, origin = relying_party()
    try:
        verified = verify_authentication_response(
            credential=payload["response"],
            expected_challenge=challenge_bytes(document["approval"]),
            expected_rp_id=host,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(pinned["public_key"]),
            credential_current_sign_count=int(pinned.get("sign_count") or 0),
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    pinned["sign_count"] = verified.new_sign_count
    save_caregiver_credential(pinned)


PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded", "ngrok-trace-id", "x-original-url")


def _host_header(request: Request) -> None:
    host_header.require(request)


def _lan_only(request: Request) -> None:
    client = request.client.host if request.client else ""
    try:
        on_lan = client == "testclient" or ipaddress.ip_address(client).is_private or ipaddress.ip_address(client).is_loopback
    except ValueError:
        on_lan = False
    if not on_lan or any(header in request.headers for header in PROXY_HEADERS):
        raise HTTPException(403, "LAN only")


@app.post("/approvals/{approval_id}/cancel")
def cancel_approval(approval_id: str, request: Request):
    _lan_only(request)
    with hold_decisions():
        document = find_approval(approval_id)
        if not document:
            raise HTTPException(404, "unknown approval")
        approval = document["approval"]
        if approval.get("used") or state_of(approval) != "pending":
            raise HTTPException(400, "approval is closed")
        approval["cancelled"] = True
        approval["used"] = True
        forget_host_code(approval_id)
        post_event(
            "approval_result",
            document["session_id"],
            document["mandate_id"],
            approval_id=approval_id,
            approved=False,
            method="cancelled",
        )
        save_decision(document)
        return public_approval(document)


@app.get("/approvals/{approval_id}/host_code")
def approval_host_code(approval_id: str, request: Request):
    """The fallback code for the relay's LAN-only Host page. Refused for proxied or non-LAN callers."""
    _lan_only(request)
    _host_header(request)
    entry = host_code(approval_id)
    if not entry:
        raise HTTPException(404, "no open code for this approval")
    return JSONResponse(entry, headers={"Cache-Control": "no-store"})


@app.post("/approvals/{approval_id}/code")
def submit_code(approval_id: str, payload: dict, request: Request):
    _host_header(request)
    with hold_decisions():
        document = find_approval(approval_id)
        if not document:
            raise HTTPException(404, "unknown approval")
        approval = document["approval"]
        if approval.get("used") or state_of(approval) != "pending":
            raise HTTPException(400, "approval is closed")
        if approval.get("attempts", 0) >= 5:
            approval["used"] = True
            forget_host_code(approval_id)
            save_decision(document)
            raise HTTPException(400, "too many attempts")
        expected = approval.get("code_hash") or ""
        presented = code_mac(str(payload.get("code", "")), approval_id)
        if not expected or not hmac.compare_digest(presented, expected):
            approval["attempts"] = approval.get("attempts", 0) + 1
            if approval["attempts"] >= 5:
                approval["used"] = True
                approval["approved"] = False
                forget_host_code(approval_id)
            save_decision(document)
            raise HTTPException(400, "code rejected")
        approval["approved"] = True
        return _finish_approval(document, "code")


def _lan_or_marker(request: Request, marker_id: str, action: str) -> None:
    if marker_matches(marker_id, request.headers.get("x-chaperone-marker", ""), action):
        return
    _lan_only(request)


@app.post("/orders/{order_id}/cancel")
def cancel_saved_order(order_id: str, request: Request, payload: dict | None = None):
    from policy.postpurchase import cancel_order

    from policy.postpurchase import MerchantRefused

    _lan_or_marker(request, order_id, "family")
    body = payload or {}
    try:
        return cancel_order(order_id, body.get("mandate_id") or "")
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, {"error": str(exc), "say_key": "cancel_too_late"}) from exc
    except MerchantRefused as exc:
        if exc.status == 409:  # paid while we asked: the station says cancel_too_late
            raise HTTPException(409, {"error": str(exc), "say_key": "cancel_too_late"}) from exc
        raise HTTPException(502, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/refunds")
def request_refund(payload: dict, request: Request):
    from policy.postpurchase import refund

    _lan_or_marker(request, "family", "family")
    try:
        return refund(payload)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/history")
def purchase_history(request: Request, mandate_id: str = "", days: int = 30):
    from policy.postpurchase import history

    _lan_or_marker(request, "family", "family")
    return history(mandate_id, days)


@app.post("/mandate/pause")
def mandate_pause(request: Request):
    from policy.postpurchase import pause_mandate

    if not marker_matches("mandate", request.headers.get("x-chaperone-marker", ""), "pause"):
        raise HTTPException(401, "sign in required")
    return pause_mandate()


@app.post("/mandate/resume/challenge")
def mandate_resume_challenge(request: Request):
    from datetime import datetime, timedelta, timezone

    from policy.approvals import new_nonce
    from policy.postpurchase import resume_challenge
    from webauthn.helpers import bytes_to_base64url

    if not marker_matches("mandate", request.headers.get("x-chaperone-marker", ""), "pause"):
        raise HTTPException(401, "sign in required")
    stored = load_mandate()
    if not stored or not load_caregiver_credential():
        raise HTTPException(404, "no signed mandate")
    nonce = new_nonce()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    app.state.resume = {"mandate_id": stored.get("mandate_id"), "nonce": nonce, "expires_at": expires_at}
    return {
        "nonce": nonce,
        "expires_at": expires_at,
        "challenge": bytes_to_base64url(resume_challenge(nonce, expires_at, stored.get("mandate_id") or "")),
    }


@app.post("/mandate/resume")
def mandate_resume(payload: dict, request: Request):
    from datetime import datetime, timezone

    from policy.postpurchase import resume_challenge
    from policy.verify_mandate import relying_party
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    if not marker_matches("mandate", request.headers.get("x-chaperone-marker", ""), "pause"):
        raise HTTPException(401, "sign in required")
    stored = load_mandate()
    # one challenge, one try: it is consumed before the passkey is checked, and it expires
    challenge = getattr(app.state, "resume", None)
    app.state.resume = None
    if not stored or not challenge or payload.get("nonce") != challenge.get("nonce"):
        raise HTTPException(400, "resume challenge missing")
    if datetime.fromisoformat(challenge["expires_at"]) < datetime.now(timezone.utc):
        raise HTTPException(400, "resume challenge expired")
    pinned = load_caregiver_credential() or {}
    host, origin = relying_party()
    try:
        verified = verify_authentication_response(
            credential=payload.get("response") or {},
            expected_challenge=resume_challenge(challenge["nonce"], challenge["expires_at"], stored.get("mandate_id") or ""),
            expected_rp_id=host,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(pinned.get("public_key") or ""),
            credential_current_sign_count=int(pinned.get("sign_count") or 0),
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    pinned["sign_count"] = verified.new_sign_count
    save_caregiver_credential(pinned)
    save_paused(False)
    post_event("mandate_resumed", "none", stored.get("mandate_id") or "none", method="passkey")
    return {"paused": False, "mandate_id": stored.get("mandate_id")}


@app.get("/decisions/{decision_id}/explain")
def explain(decision_id: str, request: Request):
    from policy.postpurchase import explain_decision

    if not marker_matches(decision_id, request.headers.get("x-chaperone-marker", ""), "explain"):
        raise HTTPException(403, "sign in required")
    try:
        return explain_decision(decision_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/mandate/visa")
def mandate_visa():
    from policy.mandate import visa_view

    return visa_view(load_mandate() or DEFAULT_MANDATE)


@app.post("/card/asa")
async def card_asa(request: Request):
    import json

    from policy.card import handle_authorization, verify_webhook

    body = await request.body()
    secret = os.environ.get("LITHIC_WEBHOOK_SECRET", "")
    if not verify_webhook(request.headers, body, secret):
        raise HTTPException(401, "webhook signature rejected")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "bad card request") from exc
    import time

    started = time.perf_counter()
    answer = handle_authorization(payload, load_mandate() or DEFAULT_MANDATE)
    ms = int((time.perf_counter() - started) * 1000)
    return JSONResponse({"result": answer["result"], "token": answer["token"]}, headers={"X-Chaperone-Ms": str(ms)})


@app.post("/card/simulate")
def card_simulate(payload: dict, request: Request):
    _lan_only(request)
    _host_header(request)
    from policy.card import simulate_swipe

    try:
        return simulate_swipe(str(payload.get("acceptor_id") or ""), int(payload.get("amount_cents") or 0))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/card/holds/{hold_id}/allow")
def card_allow(hold_id: str, request: Request):
    from policy.card import allow_hold

    if not marker_matches(hold_id, request.headers.get("x-chaperone-marker", ""), "card"):
        raise HTTPException(401, "sign in required")
    try:
        return allow_hold(hold_id)
    except KeyError as exc:
        raise HTTPException(404, "unknown hold") from exc


@app.get("/card/state")
def card_state(request: Request, mandate_id: str = ""):
    from policy.card import public_state

    _lan_or_marker(request, "family", "family")
    return public_state(mandate_id or (load_mandate() or DEFAULT_MANDATE).get("mandate_id") or "")
