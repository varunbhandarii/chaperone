"""Policy service. python -m uvicorn policy.main:app --host 0.0.0.0 --port 8001"""

from __future__ import annotations

import hashlib

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from policy.checkout import UnsignedMandate, checkout as run_checkout
from policy.engine import dollars
from policy.events import post_event
from policy.mandate import DEFAULT_MANDATE
from policy.pricing import UnknownSku
from policy.store import load_mandate, load_spent_cents, reset as reset_store, save_mandate
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
def budget(mandate_id: str = DEFAULT_MANDATE["mandate_id"]):
    del mandate_id
    spent = load_spent_cents()
    cap = int(round(DEFAULT_MANDATE["monthly_cap"] * 100))
    return {"monthly_cap": DEFAULT_MANDATE["monthly_cap"], "spent": dollars(spent), "left": dollars(cap - spent)}


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


@app.get("/decisions/{decision_id}")
def decision(decision_id: str):
    from policy.store import get_decision

    found = get_decision(decision_id)
    if not found:
        raise HTTPException(404, "unknown decision")
    return found


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


@app.post("/approvals/{approval_id}/code")
def submit_code(approval_id: str, payload: dict):
    from policy.store import get_decision, save_decision

    found = None
    for document in __import__("policy.store", fromlist=["load_decisions"]).load_decisions().values():
        approval = document.get("approval") or {}
        if approval.get("approval_id") == approval_id:
            found = document
            break
    if not found:
        raise HTTPException(404, "unknown approval")
    approval = found["approval"]
    if approval.get("used"):
        raise HTTPException(400, "code already used")
    if approval.get("attempts", 0) >= 5:
        raise HTTPException(400, "too many attempts")
    digest = hashlib.sha256(f"{payload.get('code', '')}{approval_id}".encode()).hexdigest()
    if digest != approval.get("code_hash"):
        approval["attempts"] = approval.get("attempts", 0) + 1
        if approval["attempts"] >= 5:
            approval["used"] = True
        save_decision(found)
        raise HTTPException(400, "code rejected")
    approval["used"] = True
    approval["approved"] = True
    found["decision"] = "allow"
    save_decision(found)
    return {"ok": True, "decision_id": found["decision_id"]}


@app.get("/approvals/{approval_id}")
def approval_status(approval_id: str):
    return JSONResponse({"approval_id": approval_id})
