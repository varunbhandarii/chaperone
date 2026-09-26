"""The Host's controls, LAN only: GET /host and its /host/api/* calls. Kept off the wall and out of the ledger
stream, because both are visible on the table.

GET  /host                         the page (keys: P confirm payment, Shift+R reset, A arm replay, C show code)
GET  /host/api/status              latest order, latest approval, latest session
POST /host/api/confirm-payment     sign a Pay by Link payment notification for the latest unpaid order and post it
                                   to the merchant's webhook (merchant.simulate_payment); without a webhook key,
                                   the merchant's /orders/{id}/paid callback
POST /host/api/reset               the relay's /reset fan-out
POST /host/api/arm-replay          posts replay_armed; the station plays its cached session on the next press
GET  /host/api/approval-code       the current approval's six-digit fallback code, from policy's LAN-only
                                   GET /approvals/{id}/host_code. Never logged, never on the stream.

Requests that came through a proxy or tunnel, or from outside a private network, get 403. Every POST here,
and the relay's POST /reset, also needs the header X-Chaperone-Host: 1: a cross-site form or fetch cannot set
it, so a page open in some other LAN browser (the wall display, say) cannot press the Host's buttons.
"""

from __future__ import annotations

import ipaddress
import json
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

HOST_HTML = Path(__file__).with_name("host.html")
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded", "ngrok-trace-id", "x-original-url")
NO_STORE = {"Cache-Control": "no-store"}
HOST_HEADER = "x-chaperone-host"

router = APIRouter(prefix="/host")


class _Ledger:
    """relay.ledger mounts this router, so it is imported on first use rather than at import time."""

    def __getattr__(self, name):
        from relay import ledger as module
        return getattr(module, name)


ledger = _Ledger()


def lan_only(request: Request) -> None:
    if any(h in request.headers for h in PROXY_HEADERS):
        raise HTTPException(403, "host controls are LAN only")
    host = request.client.host if request.client else ""
    if host == "testclient":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise HTTPException(403, "host controls are LAN only") from None
    if not (address.is_private or address.is_loopback):
        raise HTTPException(403, "host controls are LAN only")


def host_action(request: Request) -> None:
    """For requests that change something: LAN only, and sent by the Host page or the station on purpose."""
    lan_only(request)
    if request.headers.get(HOST_HEADER) != "1":
        raise HTTPException(403, "missing X-Chaperone-Host: 1")


def _merchant() -> str:
    return os.environ.get("MERCHANT_URL", "http://127.0.0.1:8002").rstrip("/")


def _policy() -> str:
    return os.environ.get("POLICY_URL", "http://127.0.0.1:8001").rstrip("/")


def _latest(types: set[str]) -> dict | None:
    return next((e for e in reversed(ledger.LEDGER.read_live()) if e.get("type") in types), None)


async def _orders(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(f"{_merchant()}/orders")
    r.raise_for_status()
    return r.json()


@router.get("", response_class=HTMLResponse)
def page(request: Request):
    lan_only(request)
    return HTMLResponse(HOST_HTML.read_text(encoding="utf-8"), headers=NO_STORE)


@router.get("/api/status")
async def status(request: Request):
    lan_only(request)
    order, merchant_error = None, None
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            orders = await _orders(client)
        order = orders[0] if orders else None
    except (httpx.HTTPError, ValueError) as exc:
        merchant_error = type(exc).__name__
    approval = _latest({"approval_requested", "approval_result"})
    session = _latest({"session_started", "heard", "cart_updated", "checkout_requested", "refusal"})
    summary = None
    if order:
        summary = {k: order.get(k) for k in ("order_id", "amount", "status", "session_id", "decision_id", "paid_via")}
        summary["backend"] = (order.get("payment_link") or {}).get("backend")
    return JSONResponse({
        "order": summary,
        "merchant_error": merchant_error,
        "approval": approval and {k: approval.get(k) for k in ("type", "approval_id", "amount", "expires_at",
                                                                 "approved", "method")},
        "session_id": session and session.get("session_id"),
        "seq": ledger.LEDGER.seq,
        "webhook_key": bool(os.environ.get("CYBS_WEBHOOK_KEY_ID") and os.environ.get("CYBS_WEBHOOK_KEY")),
    }, headers=NO_STORE)


@router.post("/api/confirm-payment")
async def confirm_payment(request: Request):
    """The sandbox-flow paid step: the Host says "marked paid in the sandbox flow"."""
    host_action(request)
    from merchant.simulate_payment import envelope
    from merchant.webhooks import headers_for

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            orders = await _orders(client)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, f"merchant unreachable ({type(exc).__name__})") from exc
        order = next((o for o in orders if o.get("status") == "awaiting_payment"), None)
        if order is None:
            raise HTTPException(409, "no order is waiting for payment")
        key_id, key = os.environ.get("CYBS_WEBHOOK_KEY_ID"), os.environ.get("CYBS_WEBHOOK_KEY")
        if key_id and key:
            body = json.dumps(envelope(order))
            r = await client.post(f"{_merchant()}/webhooks/cybersource", content=body,
                                  headers=headers_for(body, key_id, key))
            path = "signed webhook"
        else:
            r = await client.post(f"{_merchant()}/orders/{order['order_id']}/paid", params={"via": "host_confirmed"})
            path = "callback"
    if not r.is_success:
        raise HTTPException(502, f"merchant answered {r.status_code}: {r.text[:200]}")
    return {"ok": True, "order_id": order["order_id"], "amount": order.get("amount"), "path": path}


@router.post("/api/reset")
async def reset(request: Request):
    host_action(request)
    return await ledger.reset(request)


@router.post("/api/arm-replay")
async def arm_replay(request: Request):
    host_action(request)
    ledger.LEDGER.append({"type": "replay_armed", "session_id": "none", "mandate_id": ledger.MANDATE_ID,
                          "t": int(time.time() * 1000), "source": "relay"})
    return {"ok": True}


@router.get("/api/approval-code")
async def approval_code(request: Request):
    lan_only(request)
    latest = _latest({"approval_requested"})
    if not latest or not latest.get("approval_id"):
        raise HTTPException(404, "no approval requested")
    approval_id = str(latest["approval_id"])
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get(f"{_policy()}/approvals/{approval_id}/host_code")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"policy unreachable ({type(exc).__name__})") from exc
    if r.status_code == 404:
        raise HTTPException(404, "policy has no code for this approval")
    if not r.is_success:
        raise HTTPException(502, f"policy answered {r.status_code}")
    data = r.json()
    return JSONResponse({"approval_id": approval_id, "code": data.get("code"), "expires_at": data.get("expires_at")},
                        headers=NO_STORE)
