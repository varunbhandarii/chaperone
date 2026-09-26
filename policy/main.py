"""Policy service. python -m uvicorn policy.main:app --host 0.0.0.0 --port 8001"""

from __future__ import annotations

import hashlib

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from policy.approvals import code_mac, find_approval, public_approval, public_decision, state_of
from policy.checkout import CartRejected, ReadBackRequired, UnsignedMandate, checkout as run_checkout
from policy.checkout import send_signed_order
from policy.engine import dollars, to_cents
from policy.events import post_event
from policy.mandate import DEFAULT_MANDATE
from policy.pricing import UnknownSku
from policy.store import add_spent_cents, load_caregiver_credential, load_mandate, load_spent_cents, reset as reset_store, save_caregiver_credential, save_decision, save_mandate
from policy.verify_mandate import verify_mandate_assertion

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
        return {"signed": True, "mandate": stored}
    return {"signed": False, "mandate": DEFAULT_MANDATE, "detail": "unsigned mandate"}


def _finish_approval(document: dict, method: str) -> dict:
    approval = document["approval"]
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
    merchant_order = send_signed_order(body)
    merchant_order.pop("_signature", None)
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


@app.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, payload: dict):
    document = find_approval(approval_id)
    if not document:
        raise HTTPException(404, "unknown approval")
    approval = document["approval"]
    if approval.get("used") or state_of(approval) != "pending":
        raise HTTPException(400, "approval is closed")
    if payload.get("approved") is False:
        approval["approved"] = False
        approval["used"] = True
        post_event(
            "approval_result",
            document["session_id"],
            document["mandate_id"],
            approval_id=approval_id,
            approved=False,
            method="passkey",
        )
        save_decision(document)
        return public_approval(document)
    pinned = load_caregiver_credential()
    if not pinned:
        stored = load_mandate() or {}
        passkey = stored.get("passkey") or {}
        if passkey.get("credential_id") and passkey.get("public_key"):
            pinned = {"credential_id": passkey["credential_id"], "public_key": passkey["public_key"], "sign_count": 0}
            save_caregiver_credential(pinned)
    if not pinned or not payload.get("response"):
        return JSONResponse({"error": "passkey assertion required"}, status_code=400)
    from policy.approvals import challenge_bytes
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    host = __import__("os").environ.get("TUNNEL_HOST") or "localhost"
    origin = __import__("os").environ.get("ORIGIN") or f"https://{host}"
    try:
        verified = verify_authentication_response(
            credential=payload["response"],
            expected_challenge=challenge_bytes(approval),
            expected_rp_id=host,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(pinned["public_key"]),
            credential_current_sign_count=int(pinned.get("sign_count") or 0),
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=400)
    pinned["sign_count"] = verified.new_sign_count
    save_caregiver_credential(pinned)
    approval["approved"] = True
    return _finish_approval(document, "passkey")


@app.post("/approvals/{approval_id}/code")
def submit_code(approval_id: str, payload: dict):
    document = find_approval(approval_id)
    if not document:
        raise HTTPException(404, "unknown approval")
    approval = document["approval"]
    if approval.get("used") or state_of(approval) != "pending":
        raise HTTPException(400, "approval is closed")
    if approval.get("attempts", 0) >= 5:
        approval["used"] = True
        save_decision(document)
        raise HTTPException(400, "too many attempts")
    if code_mac(str(payload.get("code", "")), approval_id) != approval.get("code_hash"):
        approval["attempts"] = approval.get("attempts", 0) + 1
        if approval["attempts"] >= 5:
            approval["used"] = True
        save_decision(document)
        raise HTTPException(400, "code rejected")
    approval["approved"] = True
    return _finish_approval(document, "code")
