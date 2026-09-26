"""Local stand-in for the Grok Voice realtime API and the station's HTTP services, for tests without credentials.

It follows the documented event flow closely enough to exercise the station page and ws_probe.mjs:
session.created -> session.update/updated (validated: PCM rate, manual turn detection, both tools)
-> user turn (audio commit or input_text) -> response.create -> a short spoken preamble plus a
search_catalog function call -> add_to_cart -> read_cart -> the read-back; "sí" -> checkout -> the outcome.
With /mock/reset {"eager_checkout": true} the scripted model calls checkout right after add_to_cart, which the
station's read-back gate must refuse (read_back_required) before the flow continues.
It also supports response.cancel, input_audio_buffer.clear, conversation.item.truncate and
force_message. Audio is a quiet sine tone at the session's output rate.

HTTP stand-ins, in the agreed shapes: POST /session/token; catalog GET /search {"q","items"} and
GET /resolve {"q","matches"}; policy POST /screen, GET /approvals/{id}, POST /checkout (decision, decision_id, say_key, order,
approval), GET /budget, POST /orders/{id}/cancel, POST /refunds (preview, then the processor's refund shape) and
GET /history; merchant GET /orders/{id} (status, pickup code, timeline); relay POST /events and GET /audio/<clip>.
GET /mock/log returns every client event and HTTP call it saw.

Run standalone (repo root):
    .venv/Scripts/python -m uvicorn mock_realtime:app --app-dir station/kiosk/tests --port 8010
then open http://localhost:5173/?host=127.0.0.1&relay=http://127.0.0.1:8010&catalog=http://127.0.0.1:8010&policy=http://127.0.0.1:8010&ws=ws://127.0.0.1:8010/v1/realtime
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import secrets
import struct
import time

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="mock realtime + services")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

LOG: list[dict] = []
ACTIVE: set = set()  # open realtime sockets, so /mock/drop can simulate a dropped connection
RATES = {8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000}
FIRST_AUDIO_DELAY_S = 0.15
CHUNK_S = 0.04
BLOCKED_WORDS = ("tarjeta", "tarjetas", "regalo", "gift card", "gift cards")
MOCK = {"transcript": "necesito pan", "pace": 0.25, "clip": False, "eager_checkout": False,  # pace 1.0 = real time
        "approval_ttl": 90.0, "print_ok": False,
        "preparing_after": 3.0, "ready_after": 6.0}  # the merchant's timers after paid (20 s and 60 s for real)
REGULAR = {"BAK-001": 3.99}  # a promotion: the station reads savings off the receipt

# Merchant, policy approvals, relay stream and the station's print helper, all in memory.
ORDERS: dict[str, dict] = {}
APPROVALS: dict[str, dict] = {}
BUS: list[dict] = []           # relay ledger (seq-numbered), replayed and streamed by /events/stream
SUBSCRIBERS: set = set()       # asyncio queues of open streams
CACHED: dict[str, dict] = {}   # the print helper's cached sessions


def emit(event: dict) -> dict:
    stored = {**event, "seq": len(BUS) + 1, "rt": int(time.time() * 1000)}
    BUS.append(stored)
    for q in list(SUBSCRIBERS):
        q.put_nowait(stored)
    return stored


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def new_order(session_id: str, cart: dict, decision_id: str, lang: str | None) -> dict:
    order_id = new_id("o")
    ORDERS[order_id] = {"order_id": order_id, "session_id": session_id, "cart": cart, "decision_id": decision_id,
                        "lang": lang or "en", "paid_at": None, "cancelled": False, "refunds": [],
                        "pickup_code": f"{secrets.randbelow(900) + 100}", "created": time.time()}
    return {"order_id": order_id, "payment_link": f"http://127.0.0.1:8002/pay/{order_id}", "status": "link_created"}

BREAD = [
    {"sku": "BAK-002", "name": "Kroger Whole Wheat Bread", "brand": "Kroger", "category": "bakery", "mandate_category": "grocery", "price": 2.99, "size": "20 oz", "usual": False},
    {"sku": "BAK-003", "name": "Kroger Low Sodium Whole Wheat Bread", "brand": "Kroger", "category": "bakery", "mandate_category": "grocery", "price": 3.19, "size": "16 oz", "usual": False},
    {"sku": "BAK-001", "name": "Nature's Own Honey Wheat Bread", "brand": "Nature's Own", "category": "bakery", "mandate_category": "grocery", "price": 3.49, "size": "20 oz", "usual": True},
]
RX = {"sku": "RX-001", "name": "Lisinopril 10 mg, 30 tablets (pharmacy pickup)", "brand": "Corner Market Pharmacy", "category": "pharmacy_pickup", "mandate_category": "pharmacy", "price": 8.0, "size": "30 tablets", "usual": True}
ENSURE = {"sku": "NUT-002", "name": "Ensure Original Vanilla Nutrition Shake, Case", "brand": "Ensure", "category": "nutrition", "mandate_category": "grocery", "price": 52.0, "size": "24 x 8 fl oz", "usual": False}
GIFT = {"sku": "GFT-002", "name": "Apple Gift Card", "brand": "Apple", "category": "gift_card", "mandate_category": "gift_card", "price": 200.0, "size": "$200", "usual": False}
PROFILE = [
    (RX, "blood pressure medicine", "Pharmacy pickup, $8.00 copay", ("blood pressure", "presion", "presión", "mi medicina", "dawai", "दवाई")),
    (BREAD[2], "bread", "Usual brand", ("bread", "pan", "ब्रेड")),
]
BUDGET = {"monthly_cap": 300.0, "spent": 142.10, "left": 157.90}


def record(kind: str, **fields) -> None:
    LOG.append({"kind": kind, "t": int(time.time() * 1000), **fields})


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def tone(seconds: float, rate: int) -> bytes:
    n = int(seconds * rate)
    return b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n))


# ---------------------------------------------------------------- HTTP stand-ins

@app.post("/session/token")
async def token() -> dict:
    record("http", path="/session/token")
    return {"value": "mock-secret-" + secrets.token_hex(8), "expires_at": int(time.time()) + 300}


@app.get("/search")
async def search(q: str = "", limit: int = 3) -> dict:
    record("http", path="/search", q=q, limit=limit)
    ql = q.lower()
    items = (BREAD if ("bread" in ql or "pan" in ql) else [RX] if ("pressure" in ql or "presi" in ql)
             else [ENSURE] if "ensure" in ql else [GIFT] if "gift" in ql else [])
    return {"q": q, "items": items[:limit]}


@app.get("/resolve")
async def resolve(q: str = "") -> dict:
    record("http", path="/resolve", q=q)
    ql = q.lower()
    matches = [
        {"sku": item["sku"], "label": label, "matched": next(p for p in phrases if p in ql), "note": note, "confidence": 0.95, "item": item}
        for item, label, note, phrases in PROFILE
        if any(p in ql for p in phrases)
    ]
    return {"q": q, "matches": matches}


@app.get("/budget")
async def budget(mandate_id: str = "") -> dict:
    record("http", path="/budget", mandate_id=mandate_id)
    return BUDGET


@app.post("/screen")
async def screen(request: Request) -> dict:
    body = await request.json()
    record("http", path="/screen", body=body)
    text = str(body.get("text", "")).lower()
    if any(w in text for w in BLOCKED_WORDS):
        return {
            "action": "refuse",
            "hits": [{"rule_id": "R1_blocked_category", "pattern": "gift_card", "lang": "es", "term": "tarjetas de regalo"}],
            "refusal": {
                "rule_id": "R1_blocked_category",
                "spoken_key": "blocked_category",
                "patterns": ["gift_card"],
                "lang": "es",
                "text": "No puedo comprar tarjetas de regalo en esta cuenta. Ya le avisé a Priyank.",
                "audio_url": "/audio/refusal.blocked_category.es.mp3",
            },
        }
    return {"action": "proceed", "hits": [], "refusal": None}


@app.post("/checkout")
async def checkout(request: Request) -> dict:
    """allow under $40, approve above it, deny a blocked category (the policy's C7 reply shape)."""
    body = await request.json()
    record("http", path="/checkout", body=body)
    cart = body.get("cart", {})
    total = float(cart.get("total", 0))
    blocked = any(i.get("category") in ("gift_card", "prepaid_card") for i in cart.get("items", []))
    decision = "deny" if blocked else "approve" if total > 40 else "allow"
    reply = {
        "decision_id": new_id("d"),
        "session_id": body.get("session_id"),
        "mandate_id": body.get("mandate_id"),
        "decision": decision,
        "rules": [
            {"id": "R1_blocked_category", "passed": not blocked},
            {"id": "R6_approval_threshold", "passed": total <= 40, "detail": f"{total:.2f} vs 40.00"},
        ],
        "monthly_total_after": round(BUDGET["spent"] + (total if decision != "deny" else 0), 2),
        "say_key": {"allow": "ordering_now", "approve": "asking_priya", "deny": "declined"}[decision],
        "order": None,
        "approval": None,
    }
    if decision == "allow":
        reply["order"] = new_order(body.get("session_id"), cart, reply["decision_id"], body.get("lang"))
    elif decision == "approve":
        approval_id = new_id("a")
        created = time.time()
        APPROVALS[approval_id] = {
            "approval_id": approval_id, "created": created, "expires_at": iso(created + MOCK["approval_ttl"]),
            "amount": total, "merchant": "corner_market", "excerpt": body.get("transcript", "")[-200:],
            "rule": "R6_approval_threshold", "decision_id": reply["decision_id"], "order": None, "state": "pending",
            "session_id": body.get("session_id"), "cart": cart, "lang": body.get("lang"),
        }
        reply["approval"] = {"approval_id": approval_id, "expires_at": APPROVALS[approval_id]["expires_at"]}
    return reply


@app.get("/approvals/{approval_id}")
async def approval_status(approval_id: str) -> dict:
    """The policy's approval contract; expiry is computed on read."""
    a = APPROVALS.get(approval_id)
    if a is None:
        raise HTTPException(404, "unknown approval")
    if a["state"] == "pending" and time.time() > a["created"] + MOCK["approval_ttl"]:
        a["state"] = "expired"
    record("http", path=f"/approvals/{approval_id}", state=a["state"])
    return {k: a[k] for k in ("approval_id", "state", "expires_at", "amount", "merchant", "excerpt", "rule", "decision_id", "order")} | (
        {"message": a["message"]} if a.get("message") else {})


@app.post("/approvals/{approval_id}/cancel")
async def cancel_approval(approval_id: str, request: Request) -> dict:
    """The station moved on (new request or cart change): a pending approval closes as cancelled."""
    a = APPROVALS.get(approval_id)
    if a is None:
        raise HTTPException(404, "unknown approval")
    record("http", path=f"/approvals/{approval_id}/cancel", host_header=request.headers.get("x-chaperone-host"))
    if a["state"] != "pending":
        raise HTTPException(400, "approval is closed")  # policy's answer for an approval that already closed
    a["state"] = "cancelled"
    return {"approval_id": approval_id, "state": a["state"]}


@app.post("/mock/approvals/{approval_id}/{verdict}")
async def mock_decide(approval_id: str, verdict: str, request: Request) -> dict:
    """Stands in for the caregiver's passkey approval: approve places the order, reject may carry a message."""
    a = APPROVALS[approval_id]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if verdict == "approve":
        a["state"] = "approved"
        a["order"] = new_order(a["session_id"], a["cart"], a["decision_id"], a["lang"])
    else:
        a["state"] = "rejected"
        a["message"] = (body or {}).get("message")
    return {"approval_id": approval_id, "state": a["state"], "order": a["order"]}


@app.get("/orders/{order_id}/receipt")
async def receipt(order_id: str, lang: str | None = None) -> dict:
    o = ORDERS.get(order_id)
    if o is None:
        raise HTTPException(404, "unknown order")
    # The merchant's shape: money as strings, price = line total with unit_price beside it, "after 3pm".
    items = [{"name": i["name"], "qty": i["qty"], "price": f"{i['price'] * i['qty']:.2f}", "unit_price": f"{i['price']:.2f}",
              "sku": i["sku"]} for i in o["cart"].get("items", [])]
    return {"merchant": "Corner Market", "items": items, "total": f"{float(o['cart'].get('total', 0)):.2f}", "currency": "USD",
            "pickup": "after 3pm", "order_id": order_id, "decision_id": o["decision_id"], "session_id": o["session_id"],
            "status": "paid" if o["paid_at"] else "awaiting_payment", "paid_at": o["paid_at"],
            "session_url": f"https://tunnel.example/s/{o['session_id']}", "lang": lang or o["lang"],
            "sandbox_note": "Paid in the Visa sandbox. No real money.",
            "savings": f"{sum((REGULAR.get(i['sku'], i['price']) - i['price']) * i['qty'] for i in o['cart'].get('items', [])):.2f}",
            "loyalty_points": int(float(o["cart"].get("total", 0))), "pickup_code": o["pickup_code"]}


def order_status(o: dict) -> str:
    """The merchant's lifecycle, computed on read: awaiting_payment, paid, preparing, ready_for_pickup; side exits."""
    if o["cancelled"]:
        return "cancelled"
    if o["refunds"]:
        refunded = sum(r["amount"] for r in o["refunds"])
        return "refunded" if refunded >= float(o["cart"].get("total", 0)) - 0.005 else "partially_refunded"
    if not o["paid_at"]:
        return "awaiting_payment"
    age = time.time() - datetime.fromisoformat(o["paid_at"].replace("Z", "+00:00")).timestamp()
    return "ready_for_pickup" if age >= MOCK["ready_after"] else "preparing" if age >= MOCK["preparing_after"] else "paid"


@app.get("/orders/{order_id}")
async def merchant_order(order_id: str) -> dict:
    o = ORDERS.get(order_id)
    if o is None:
        raise HTTPException(404, "unknown order")
    status = order_status(o)
    record("http", path=f"/orders/{order_id}", status=status)
    timeline = [{"status": "awaiting_payment", "at": iso(o["created"])}]
    if o["paid_at"]:
        timeline.append({"status": "paid", "at": o["paid_at"]})
    if status in ("preparing", "ready_for_pickup"):
        timeline.append({"status": "preparing", "at": o["paid_at"]})
    if status == "ready_for_pickup":
        timeline.append({"status": "ready_for_pickup", "at": o["paid_at"]})
    return {"order_id": order_id, "status": status, "pickup_code": o["pickup_code"], "amount": f"{float(o['cart'].get('total', 0)):.2f}",
            "timeline": timeline, "refunds": o["refunds"]}


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, request: Request):
    """Policy's cancel: only an unpaid order; the payment link goes INACTIVE."""
    o = ORDERS.get(order_id)
    record("http", path=f"/orders/{order_id}/cancel")
    if o is None:
        raise HTTPException(404, "unknown order")
    if order_status(o) != "awaiting_payment":
        return Response(json.dumps({"error": "order is paid", "say_key": "cancel_too_late"}), status_code=409, media_type="application/json")
    o["cancelled"] = True
    emit({"type": "order_status", "session_id": o["session_id"], "mandate_id": "m_ruth_2026_09", "t": int(time.time() * 1000),
          "source": "merchant", "order_id": order_id, "status": "cancelled"})
    return {"order_id": order_id, "status": "cancelled", "link_status": "INACTIVE"}


SCAM_REFUND = ("difference", "diferencia", "gift card", "tarjeta de regalo", "refunded me too much", "de más", "zyada refund", "फर्क")


@app.post("/refunds")
async def refunds(request: Request):
    """Policy's refund: RF1 paid order, RF4 no prescription returns, RF5 refund-scam words; preview, then the refund."""
    body = await request.json()
    record("http", path="/refunds", body=body)
    o = ORDERS.get(body.get("order_id", ""))
    rules = []
    if o is None or not o["paid_at"]:
        return {"decision": "deny", "say_key": "declined", "rules": [{"id": "RF1_order_owned", "passed": False}]}
    lines = [i for i in o["cart"].get("items", []) if not body.get("sku") or i["sku"] == body["sku"]]
    if str(body.get("sku") or "").startswith("RX") or any(i.get("category") in ("pharmacy", "pharmacy_pickup") or i["sku"].startswith("RX") for i in lines):
        return {"decision": "deny", "say_key": "refund_not_allowed_rx", "rules": [{"id": "RF4_returnable", "passed": False}]}
    if any(w in str(body.get("transcript", "")).lower() for w in SCAM_REFUND):
        return {"decision": "deny", "say_key": "refund_scam", "rules": [{"id": "RF5_screen", "passed": False}]}
    qty = int(body.get("qty") or 0)
    items = [{"name": i["name"], "qty": qty or i["qty"], "amount": round(i["price"] * (qty or i["qty"]), 2)} for i in lines]
    amount = round(sum(i["amount"] for i in items), 2)
    if not body.get("confirmed"):
        return {"preview": {"amount": amount, "card_last4": "1111", "items": items}, "say": f"${amount:.2f} back to your card ending 1111. Shall I?"}
    refund = {"id": new_id("rf"), "status": "PENDING", "reconciliationId": secrets.token_hex(6).upper(),
              "refundAmountDetails": {"refundAmount": f"{amount:.2f}", "currency": "USD"},
              "processorInformation": {"responseCode": "100", "approvalCode": "831000"}, "source": "sandbox-processor-stub"}
    o["refunds"].append({"id": refund["id"], "amount": amount, "status": "PENDING", "at": iso(time.time())})
    return {"decision": "allow", "rules": rules, "refund": refund}


@app.get("/history")
async def history(mandate_id: str = "", days: int = 30) -> dict:
    record("http", path="/history", days=days)
    orders = [{"order_id": k, "at": v["paid_at"] or iso(v["created"]), "total": v["cart"].get("total"), "status": order_status(v),
               "items": v["cart"].get("items", [])} for k, v in ORDERS.items()]
    refunds = [{**r, "order_id": k} for k, v in ORDERS.items() for r in v["refunds"]]
    return {"orders": orders, "refunds": refunds, "refusals": [], "totals": {"spent": 142.10}}


@app.post("/mock/pay/{order_id}")
async def mock_pay(order_id: str) -> dict:
    """The merchant's paid event, as after the hosted page or the sandbox callback."""
    o = ORDERS[order_id]
    o["paid_at"] = iso(time.time())
    return emit({"type": "paid", "session_id": o["session_id"], "mandate_id": "m_ruth_2026_09", "t": int(time.time() * 1000),
                 "source": "merchant", "order_id": order_id, "total": o["cart"].get("total"), "via": "mock"})


@app.get("/mock/orders")
async def mock_orders() -> dict:
    return {"orders": ORDERS, "approvals": {k: v["state"] for k, v in APPROVALS.items()}}


@app.post("/reset")
async def relay_reset() -> dict:
    """The relay's reset: clears the stand-ins and posts a reset event."""
    ORDERS.clear()
    APPROVALS.clear()
    record("http", path="/reset")
    emit({"type": "reset", "session_id": "none", "mandate_id": "m_ruth_2026_09", "t": int(time.time() * 1000), "source": "relay"})
    return {"ok": True}


@app.get("/events/stream")
async def stream(request: Request, types: str | None = None, session_id: str | None = None,
                 last_event_id: int | None = None, once: bool = False) -> StreamingResponse:
    """The relay's SSE stream: replays the ledger after last_event_id, then follows it (unless once)."""
    wanted = {t for t in (types or "").split(",") if t} or None
    header = request.headers.get("last-event-id")
    after = int(header) if header and header.isdigit() else (last_event_id or 0)

    def matches(ev: dict) -> bool:
        return (not session_id or ev.get("session_id") == session_id) and (not wanted or ev.get("type") in wanted)

    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        if not once:
            SUBSCRIBERS.add(q)
        try:
            yield "retry: 1000\n\n"
            for ev in list(BUS):
                if ev["seq"] > after and matches(ev):
                    yield f"id: {ev['seq']}\ndata: {json.dumps(ev)}\n\n"
            if once:
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if matches(ev):
                    yield f"id: {ev['seq']}\ndata: {json.dumps(ev)}\n\n"
        finally:
            SUBSCRIBERS.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/print")
async def print_receipt(request: Request) -> dict:
    """The station print helper: {ok: true} only when enabled with /mock/reset {"print_ok": true}."""
    body = await request.json()
    record("http", path="/print", body=body)
    return {"ok": True, "via": "printer", "ms": 40} if MOCK["print_ok"] else {"ok": False, "reason": "no printer (mock)", "ms": 5}


@app.put("/cached/{lang}")
async def put_cached(lang: str, request: Request) -> dict:
    CACHED[lang] = await request.json()
    return {"ok": True}


@app.get("/cached/{lang}")
async def get_cached(lang: str):
    if lang not in CACHED:
        raise HTTPException(404, f"no cached session for {lang}")
    return CACHED[lang]


@app.post("/events")
async def events(request: Request) -> dict:
    body = await request.json()
    record("event", body=body)
    emit(body)
    return {"ok": True}


@app.get("/audio/{name}")
@app.get("/warnings/{name}")
async def warning_clip(name: str) -> Response:
    """A refusal clip (WAV tone) when enabled with /mock/reset {"clip": true}; 404 otherwise."""
    record("http", path=f"/audio/{name}", served=MOCK["clip"])
    if not MOCK["clip"]:
        return Response(status_code=404)
    pcm = tone(0.6, 24000)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16) + b"data" + struct.pack("<I", len(pcm))
    return Response(header + pcm, media_type="audio/wav")


@app.post("/mock/drop")
async def mock_drop() -> dict:
    """Closes every open realtime socket (code 1011), as a network drop would."""
    n = len(ACTIVE)
    for ws in list(ACTIVE):
        await ws.close(code=1011)
    record("mock", note=f"dropped {n} socket(s)")
    return {"dropped": n}


@app.get("/mock/log")
async def mock_log() -> list[dict]:
    return LOG


@app.post("/mock/reset")
async def mock_reset(request: Request) -> dict:
    LOG.clear()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        if body.get("transcript"):
            MOCK["transcript"] = str(body["transcript"])
        if body.get("pace") is not None:
            MOCK["pace"] = float(body["pace"])
        if body.get("clip") is not None:
            MOCK["clip"] = bool(body["clip"])
        MOCK["eager_checkout"] = bool(body.get("eager_checkout", False))
        MOCK["approval_ttl"] = float(body.get("approval_ttl", 90.0))
        MOCK["print_ok"] = bool(body.get("print_ok", False))
        MOCK["eager_refund"] = bool(body.get("eager_refund", False))
    return {"ok": True}


# ---------------------------------------------------------------- realtime

class Session:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.lock = asyncio.Lock()
        self.config: dict = {}
        self.rate = 24000
        self.buffer_bytes = 0
        self.task: asyncio.Task | None = None
        self.response_id: str | None = None
        self.tool_outputs: list[dict] = []
        self.last_user = ""
        self.items: list[str] = []

    async def send(self, event: dict) -> None:
        event.setdefault("event_id", new_id("event"))
        async with self.lock:
            await self.ws.send_text(json.dumps(event))

    async def error(self, message: str, param: str | None = None, etype: str = "invalid_request_error") -> None:
        await self.send({"type": "error", "error": {"type": etype, "code": etype, "message": message, "param": param}})

    # -- session.update validation mirrors the documented schema
    async def session_update(self, session: dict) -> None:
        audio = session.get("audio", {})
        rin = audio.get("input", {}).get("format", {}).get("rate")
        rout = audio.get("output", {}).get("format", {}).get("rate")
        if rin not in RATES or rout not in RATES:
            return await self.error(f"unsupported PCM rate {rin}/{rout}", "session.audio.format.rate")
        td = session.get("turn_detection", "missing")
        if not (td is None or (isinstance(td, dict) and td.get("type") is None)):
            return await self.error("mock expects manual turn detection", "session.turn_detection")
        names = [t.get("name") for t in session.get("tools", []) if t.get("type") == "function"]
        missing = [n for n in ("search_catalog", "add_to_cart", "read_cart", "checkout", "order_status", "cancel_order",
                               "request_refund", "purchase_history") if n not in names]
        if missing:
            return await self.error(f"tools missing: {', '.join(missing)}", "session.tools")
        self.config = session
        self.rate = int(rout)
        await self.send({
            "type": "session.updated",
            "session": {
                "id": new_id("sess"), "object": "realtime.session", "model": "grok-voice-think-fast-2.0",
                "voice": session.get("voice"), "instructions": session.get("instructions", ""),
                "turn_detection": {"type": None}, "tools": session.get("tools", []),
            },
        })

    async def commit(self) -> None:
        if self.buffer_bytes == 0:
            return await self.error("input audio buffer is empty", "input_audio_buffer")
        item_id = new_id("item")
        prev = self.items[-1] if self.items else None
        self.items.append(item_id)
        seconds = self.buffer_bytes / 2 / self.rate
        self.buffer_bytes = 0
        await self.send({"type": "input_audio_buffer.committed", "item_id": item_id, "previous_item_id": prev})
        self.last_user = MOCK["transcript"]
        record("mock", note=f"committed {seconds:.2f}s of audio")

        async def transcribe():
            await asyncio.sleep(0.2)
            await self.send({"type": "conversation.item.input_audio_transcription.updated", "item_id": item_id, "content_index": 0, "transcript": self.last_user.split(" ")[0]})
            await asyncio.sleep(0.15)
            await self.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": item_id, "content_index": 0, "transcript": self.last_user})

        asyncio.create_task(transcribe())

    async def speak(self, text: str, seconds: float, tool_call: dict | None = None) -> None:
        rid = new_id("resp")
        self.response_id = rid
        item_id = new_id("item")
        try:
            await self.send({"type": "response.created", "response": {"id": rid, "object": "realtime.response", "status": "in_progress", "output": []}})
            await asyncio.sleep(FIRST_AUDIO_DELAY_S)
            await self.send({"type": "response.output_item.added", "response_id": rid, "output_index": 0, "item": {"id": item_id, "object": "realtime.item", "type": "message", "role": "assistant", "status": "in_progress", "content": []}})
            pcm = tone(seconds, self.rate)
            step = int(CHUNK_S * self.rate) * 2
            words = text.split(" ")
            for i, off in enumerate(range(0, len(pcm), step)):
                await self.send({"type": "response.output_audio.delta", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0, "delta": base64.b64encode(pcm[off:off + step]).decode()})
                if i < len(words):
                    await self.send({"type": "response.output_audio_transcript.delta", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0, "delta": (" " if i else "") + words[i]})
                await asyncio.sleep(CHUNK_S * MOCK["pace"])
            rest = " ".join(words[len(range(0, len(pcm), step)):])
            if rest:
                await self.send({"type": "response.output_audio_transcript.delta", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0, "delta": " " + rest})
            await self.send({"type": "response.output_audio.done", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0})
            await self.send({"type": "response.output_audio_transcript.done", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0, "transcript": text})
            if tool_call:
                fc_id, call_id = new_id("item"), new_id("call")
                await self.send({"type": "response.output_item.added", "response_id": rid, "output_index": 1, "item": {"id": fc_id, "object": "realtime.item", "type": "function_call", "status": "in_progress", "call_id": call_id, "name": tool_call["name"]}})
                await self.send({"type": "response.function_call_arguments.done", "response_id": rid, "item_id": fc_id, "output_index": 1, "call_id": call_id, "name": tool_call["name"], "arguments": json.dumps(tool_call["arguments"])})
            self.response_id = None  # a response.create right after response.done must be accepted
            await self.send({"type": "response.done", "response": {"id": rid, "object": "realtime.response", "status": "completed", "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}}})
        except asyncio.CancelledError:
            await self.send({"type": "response.done", "response": {"id": rid, "object": "realtime.response", "status": "cancelled"}})
            raise
        finally:
            if self.response_id == rid:
                self.response_id = None

    def start(self, coro) -> None:
        self.response_id = "starting"
        self.task = asyncio.create_task(coro)

    async def response_create(self, response: dict | None) -> None:
        if self.response_id is not None:
            return await self.error("a response is already in progress", "response")
        instructions = (response or {}).get("instructions")
        if instructions:
            text = instructions.split("nothing else:", 1)[-1].strip()
            return self.start(self.speak(text, 0.8))
        if self.tool_outputs and json.loads(self.tool_outputs[-1].get("output") or "{}").get("status") == "preview":
            # a refund preview was read out: remember it, so the shopper's "sí" confirms that refund
            self.pending_refund = json.loads(self.tool_outputs[-1].get("output"))
        if self.tool_outputs:
            # The scripted model: search -> add the usual -> read back (or, eager, checkout first) -> speak.
            outputs, self.tool_outputs = self.tool_outputs, []
            data = json.loads(outputs[-1].get("output") or "{}")
            if data.get("refused"):
                return self.start(self.speak(data.get("say", ""), 1.0))
            if "items" in data and data.get("status") != "preview":  # a refund preview lists items too
                items = data["items"]
                if not items:
                    return self.start(self.speak("No lo encontré.", 0.6))
                pick = next((i for i in items if i.get("usual")), items[0])
                names = ", ".join(f"{i['name']} {i['price']}" for i in items)
                return self.start(self.speak(f"Tengo {names}. Le pongo el de siempre.", 1.2, {"name": "add_to_cart", "arguments": {"sku": pick["sku"], "qty": 1}}))
            if data.get("ok") and "added" in data:
                if MOCK["eager_checkout"]:
                    return self.start(self.speak("Lo pido.", 0.4, {"name": "checkout", "arguments": {}}))
                return self.start(self.speak("Muy bien.", 0.4, {"name": "read_cart", "arguments": {}}))
            if data.get("error") == "read_back_required":
                return self.start(self.speak("Primero se lo leo.", 0.4, {"name": "read_cart", "arguments": {}}))
            if "lines" in data and "say" in data:
                return self.start(self.speak(data["say"], 1.2))
            if "status" in data and "say" in data:
                return self.start(self.speak(data["say"], 0.8))
            if "left" in data and "say" in data:
                return self.start(self.speak(data["say"], 0.8))
            if "orders" in data:
                return self.start(self.speak(f"Tiene {len(data['orders'])} pedidos recientes.", 0.8))
            if data.get("error") == "refund_confirm_required":
                return self.start(self.speak("Primero se lo confirmo.", 0.4, {"name": "request_refund", "arguments": {"sku": "BAK-001", "reason": "return", "confirmed": False}}))
            return self.start(self.speak("Lo siento, hubo un problema.", 0.8))
        words = set(self.last_user.lower().replace(",", " ").replace(".", " ").replace("?", " ").replace("¿", " ").split())
        low = self.last_user.lower()
        if words & {"sí", "si", "yes"} and getattr(self, "pending_refund", None):
            self.pending_refund = None
            return self.start(self.speak("Un momento.", 0.4, {"name": "request_refund", "arguments": {"sku": "BAK-001", "reason": "return", "confirmed": True}}))
        if "devolver" in low or "return" in low or "wapas" in low:
            return self.start(self.speak("Un momento.", 0.4, {"name": "request_refund", "arguments": {"sku": "RX-001" if "medicina" in low else "BAK-001", "reason": "return", "confirmed": MOCK.get("eager_refund", False)}}))
        if "cancel" in low:
            return self.start(self.speak("Un momento.", 0.4, {"name": "cancel_order", "arguments": {}}))
        if "dónde" in low or "donde" in low or "where" in low:
            return self.start(self.speak("Un momento.", 0.4, {"name": "order_status", "arguments": {}}))
        if "compré" in low or "compre" in low or "bought" in low:
            return self.start(self.speak("Un momento.", 0.4, {"name": "purchase_history", "arguments": {"days": 30}}))
        if words & {"sí", "si", "yes"}:
            return self.start(self.speak("Un momento.", 0.4, {"name": "checkout", "arguments": {}}))
        if words & {"cuánto", "cuanto", "queda", "left"}:
            return self.start(self.speak("Un momento.", 0.4, {"name": "budget_left", "arguments": {}}))
        if "ensure" in self.last_user.lower():
            return self.start(self.speak("Un momento.", 0.4, {"name": "search_catalog", "arguments": {"query": "Ensure"}}))
        if "pan" in self.last_user.lower() or "bread" in self.last_user.lower():
            return self.start(self.speak("Un momento.", 0.4, {"name": "search_catalog", "arguments": {"query": "bread"}}))
        return self.start(self.speak("¿En qué le puedo ayudar?", 0.8))

    async def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()


@app.websocket("/v1/realtime")
async def realtime(ws: WebSocket) -> None:
    protocols = ws.scope.get("subprotocols") or []
    secret = next((p for p in protocols if p.startswith("xai-client-secret.")), None)
    record("ws_connect", model=ws.query_params.get("model"), subprotocol_ok=bool(secret))
    if not secret:
        await ws.close(code=4401)
        return
    await ws.accept(subprotocol=secret)
    ACTIVE.add(ws)
    s = Session(ws)
    conversation_id = ws.query_params.get("conversation_id") or new_id("conv")
    record("ws_connect_conversation", conversation_id=conversation_id, resumed=bool(ws.query_params.get("conversation_id")))
    await s.send({"type": "session.created", "session": {"id": new_id("sess"), "object": "realtime.session", "model": ws.query_params.get("model"), "voice": "ara", "turn_detection": {"type": "server_vad"}}})
    await s.send({"type": "conversation.created", "conversation": {"id": conversation_id, "object": "realtime.conversation"}})
    try:
        while True:
            ev = json.loads(await ws.receive_text())
            et = ev.get("type")
            if et == "input_audio_buffer.append":
                n = len(base64.b64decode(ev.get("audio", "")))
                s.buffer_bytes += n
                record("client", type=et, bytes=n)
                continue
            record("client", type=et, event=ev)
            if et == "session.update":
                await s.session_update(ev.get("session", {}))
            elif et == "input_audio_buffer.commit":
                await s.commit()
            elif et == "input_audio_buffer.clear":
                s.buffer_bytes = 0
                await s.send({"type": "input_audio_buffer.cleared"})
            elif et == "conversation.item.create":
                item = ev.get("item", {})
                if item.get("type") == "function_call_output":
                    s.tool_outputs.append(item)
                elif item.get("type") == "force_message":
                    s.start(s.speak(item["content"][0]["text"], 1.0))
                elif item.get("type") == "message" and item.get("role") == "user":
                    s.last_user = " ".join(c.get("text", "") for c in item.get("content", []))
                await s.send({"type": "conversation.item.added", "item": {"id": new_id("item"), "type": item.get("type"), "role": item.get("role")}})
            elif et == "response.create":
                await s.response_create(ev.get("response"))
            elif et == "response.cancel":
                await s.cancel()
            elif et == "conversation.item.truncate":
                await s.send({"type": "conversation.item.truncated", "item_id": ev.get("item_id"), "content_index": 0, "audio_end_ms": ev.get("audio_end_ms", 0)})
            else:
                await s.error(f"unsupported event {et}", etype="invalid_event")
    except (WebSocketDisconnect, RuntimeError):
        record("ws_disconnect")
        await s.cancel()
    finally:
        ACTIVE.discard(ws)
