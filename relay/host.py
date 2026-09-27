"""The Host's controls, LAN only: GET /host and its /host/api/* calls. Kept off the wall and out of the ledger
stream, because both are visible on the table.

GET  /host                         the page (keys: P confirm payment, U picked up, Shift+R reset, A arm replay,
                                   C show code)
GET  /host/api/status              latest order, latest approval, latest session
POST /host/api/confirm-payment     sign a Pay by Link payment notification for the latest unpaid order and post it
                                   to the merchant's webhook (merchant.simulate_payment); without a webhook key,
                                   the merchant's /orders/{id}/paid callback
POST /host/api/picked-up           the latest paid order waiting for pickup -> the merchant's /orders/{id}/picked-up
POST /host/api/reset               the relay's /reset fan-out
POST /host/api/arm-replay          posts replay_armed; the station plays its cached session on the next press
GET  /host/api/approval-code       the current approval's six-digit fallback code, from policy's LAN-only
                                   GET /approvals/{id}/host_code. Never logged, never on the stream.
POST /host/api/swipe               {acceptor_id, amount_cents}: the card terminal's Tap card -> policy
                                   /card/simulate (Lithic simulates the swipe; our /card/asa decides it)
GET  /terminal                     the card terminal tablet page (a store's card reader), LAN only

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

from common import host_header, tls

HOST_HTML = Path(__file__).with_name("host.html")
TERMINAL_HTML = Path(__file__).with_name("terminal.html")
SWIPE_TIMEOUT_S = 12.0  # Lithic's simulate answers after our /card/asa decided; its own record lags ~1 s
MAX_SWIPE_CENTS = 1_000_000
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded", "ngrok-trace-id", "x-original-url")
NO_STORE = {"Cache-Control": "no-store"}
HOST_HEADER = "x-chaperone-host"

router = APIRouter(prefix="/host")
terminal_router = APIRouter()


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
        async with httpx.AsyncClient(verify=tls.context(), timeout=1.0) as client:
            orders = await _orders(client)
        order = orders[0] if orders else None
    except (httpx.HTTPError, ValueError) as exc:
        merchant_error = type(exc).__name__
    approval = _latest({"approval_requested", "approval_result"})
    session = _latest({"session_started", "heard", "cart_updated", "checkout_requested", "refusal"})
    summary = None
    if order:
        summary = {k: order.get(k) for k in ("order_id", "amount", "status", "session_id", "decision_id", "paid_via",
                                             "store", "fulfilment", "pickup_code")}
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

    async with httpx.AsyncClient(verify=tls.context(), timeout=5.0) as client:
        try:
            orders = await _orders(client)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, f"merchant unreachable ({type(exc).__name__})") from exc
        order = next((o for o in orders if o.get("status") == "awaiting_payment"), None)
        if order is None:
            raise HTTPException(409, "no order is waiting for payment")
        # A cart that spanned stores is one signed order per store under one decision: one press pays them all.
        batch = [o for o in orders if o.get("status") == "awaiting_payment"
                 and order.get("decision_id") and o.get("decision_id") == order.get("decision_id")] or [order]
        key_id, key = os.environ.get("CYBS_WEBHOOK_KEY_ID"), os.environ.get("CYBS_WEBHOOK_KEY")
        path = "signed webhook" if key_id and key else "callback"
        for o in batch:
            if key_id and key:
                body = json.dumps(envelope(o))
                r = await client.post(f"{_merchant()}/webhooks/cybersource", content=body,
                                      headers=headers_for(body, key_id, key))
            else:
                r = await client.post(f"{_merchant()}/orders/{o['order_id']}/paid", params={"via": "host_confirmed"},
                                      headers=host_header.HEADERS)
            if not r.is_success:
                raise HTTPException(502, f"merchant answered {r.status_code} for {o['order_id']}: {r.text[:200]}")
    total = sum(float(o.get("amount") or 0) for o in batch)
    return {"ok": True, "order_id": order["order_id"], "order_ids": [o["order_id"] for o in batch],
            "amount": f"{total:.2f}", "path": path}


PICKUP_READY = ("paid", "preparing", "ready_for_pickup")


@router.post("/api/picked-up")
async def picked_up(request: Request):
    """Ruth (or Priyank) collected the order at the counter: the newest paid order that isn't picked up yet."""
    host_action(request)
    async with httpx.AsyncClient(verify=tls.context(), timeout=5.0) as client:
        try:
            orders = await _orders(client)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, f"merchant unreachable ({type(exc).__name__})") from exc
        order = next((o for o in orders if o.get("pickup_code") and o.get("fulfilment") in PICKUP_READY), None)
        if order is None:
            raise HTTPException(409, "no paid order is waiting for pickup")
        r = await client.post(f"{_merchant()}/orders/{order['order_id']}/picked-up", headers=host_header.HEADERS)
    if not r.is_success:
        raise HTTPException(502, f"merchant answered {r.status_code}: {r.text[:200]}")
    return {"ok": True, "order_id": order["order_id"], "pickup_code": order.get("pickup_code"),
            "store": order.get("store")}


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
        async with httpx.AsyncClient(verify=tls.context(), timeout=1.0) as client:
            r = await client.get(f"{_policy()}/approvals/{approval_id}/host_code", headers={HOST_HEADER: "1"})
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"policy unreachable ({type(exc).__name__})") from exc
    if r.status_code == 404:
        raise HTTPException(404, "policy has no code for this approval")
    if not r.is_success:
        raise HTTPException(502, f"policy answered {r.status_code}")
    data = r.json()
    return JSONResponse({"approval_id": approval_id, "code": data.get("code"), "expires_at": data.get("expires_at")},
                        headers=NO_STORE)


def terminal_stores() -> list[dict]:
    from common import merchants

    return merchants.registry().get("card_terminal_stores") or []


@terminal_router.get("/terminal", response_class=HTMLResponse)
def terminal_page(request: Request):
    lan_only(request)
    stores = json.dumps(terminal_stores()).replace("</", "<\\/")
    return HTMLResponse(TERMINAL_HTML.read_text(encoding="utf-8").replace("__STORES__", stores), headers=NO_STORE)


@router.post("/api/swipe")
async def swipe(request: Request):
    """The terminal's Tap card. Policy asks Lithic to simulate the swipe; the answer is our own /card/asa's."""
    host_action(request)
    try:
        body = await request.json()
        acceptor_id, amount_cents = str(body["acceptor_id"]), int(body["amount_cents"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, "send {acceptor_id, amount_cents}") from exc
    store = next((s for s in terminal_stores() if s.get("acceptor_id") == acceptor_id), None)
    if store is None:
        raise HTTPException(404, "unknown store")
    if not 1 <= amount_cents <= MAX_SWIPE_CENTS:
        raise HTTPException(422, "amount out of range")
    started = time.perf_counter()
    async with httpx.AsyncClient(verify=tls.context(), timeout=SWIPE_TIMEOUT_S) as client:
        try:
            r = await client.post(f"{_policy()}/card/simulate", headers=host_header.HEADERS,
                                  json={"acceptor_id": acceptor_id, "amount_cents": amount_cents})
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"policy unreachable ({type(exc).__name__})") from exc
    try:
        answer = r.json()
    except ValueError:
        answer = {}
    if not r.is_success:
        detail = answer.get("detail") if isinstance(answer, dict) else None
        raise HTTPException(r.status_code if r.status_code in (404, 503) else 502, detail or f"policy answered {r.status_code}")
    return {**answer, "store": answer.get("store") or store["name"], "mcc": store["mcc"],
            "round_trip_ms": round((time.perf_counter() - started) * 1000)}
