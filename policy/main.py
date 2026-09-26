"""Policy service. python -m uvicorn policy.main:app --host 0.0.0.0 --port 8001"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from policy.approvals import code_mac, find_approval, forget_host_code, host_code, marker_matches, public_approval, public_decision, state_of
from policy.checkout import CartRejected, ReadBackRequired, UnsignedMandate, checkout as run_checkout
from policy.checkout import send_signed_order
from policy.engine import dollars, to_cents
from policy.events import post_event
from policy.mandate import DEFAULT_MANDATE
from policy.pricing import UnknownSku
from policy.store import add_spent_cents, hold_decisions, load_caregiver_credential, load_mandate, load_spent_cents, reset as reset_store, save_caregiver_credential, save_decision, save_mandate
from policy.verify_mandate import relying_party, verify_mandate_assertion

app = FastAPI(title="Chaperone policy")

try:
    from policy.screen import router as screen_router

    app.include_router(screen_router)
except ImportError:
    pass


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
def reset():
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
    if stored:
        public = {key: value for key, value in stored.items() if key != "passkey"}
        credential_id = (stored.get("passkey") or {}).get("credential_id")
        return {"signed": True, "credential_id": credential_id, "mandate": public}
    return {"signed": False, "mandate": DEFAULT_MANDATE, "detail": "unsigned mandate"}


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
def list_approvals():
    from policy.store import load_decisions

    pending = []
    for document in load_decisions().values():
        if not document.get("approval"):
            continue
        view = public_approval(document)
        if view["state"] == "pending":
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
    entry = host_code(approval_id)
    if not entry:
        raise HTTPException(404, "no open code for this approval")
    return JSONResponse(entry, headers={"Cache-Control": "no-store"})


@app.post("/approvals/{approval_id}/code")
def submit_code(approval_id: str, payload: dict):
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
