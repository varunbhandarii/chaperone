"""The merchant service (port 8002): every storefront in contracts/merchants.json (Corner Market, Parkside
Pharmacy, Main Street Home, Peachtree Power). The signed order's cart.merchant picks the storefront (default
corner_market); each storefront makes its Pay by Link on its own Cybersource account, or on the main one, tagged.

    python -m uvicorn merchant.orders:app --host 0.0.0.0 --port 8002

POST /orders             signed order from the checkout tool -> verify -> price from catalog -> payment link
                         (409 if the decision already has an order)
GET  /orders[/{id}]      order state with its timeline (wall, policy); ?session_id= filters the list
POST /orders/{id}/cancel           signed (five checks): an unpaid order's Pay by Link goes INACTIVE, order cancelled
POST /orders/{id}/refunds          signed (five checks): refund a line to the original card (sandbox processor stub)
POST /orders/{id}/picked-up        the Host marks the pickup done (X-Chaperone-Host: 1)
GET  /orders/{id}/receipt[?lang=]  what the station prints; session_url is the receipt's QR code
POST /orders/{id}/paid   the Host marks paid (X-Chaperone-Host: 1)
GET  /pay/{link_id}      mock hosted payment page (MOCK_VISA=1 or fallback)
GET  /checkout/{id}      CARD_AUTH=1: our checkout page with a real Cybersource sandbox card authorization
GET  /panel              merchant verification panel data for the wall
POST /reset              demo reset (X-Chaperone-Host: 1): clear orders and the panel's event buffer (nonces are kept)
POST /webhooks/cybersource            signed Cybersource webhook -> order paid (see merchant.webhooks)
"""

import asyncio
import datetime
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from catalog.search import Catalog  # noqa: E402
from common import host_header, merchants, tls  # noqa: E402
from merchant import aftercare, biller, card_auth, events, risk, webhooks  # noqa: E402
from merchant.verify import verify_request  # noqa: E402
from merchant.visa import LineItem, StorefrontLinks, money  # noqa: E402

PUBLIC_URL = os.environ.get("MERCHANT_PUBLIC_URL", "http://192.168.8.10:8002")
MAX_QTY = 24
# CARD_AUTH=1: orders also get our own checkout page that runs a real sandbox card authorization
# (merchant.card_auth) and marks the order paid only on AUTHORIZED. Default off: behavior unchanged.
CARD_AUTH = os.environ.get("CARD_AUTH") == "1"
RECEIPT_LANGS = {"en", "es", "hi"}

catalog = Catalog.load()
storefront_links = StorefrontLinks(PUBLIC_URL)
payment_links = storefront_links.links()  # Corner Market's; with MOCK_VISA every store shares this mock
ORDERS: dict[str, dict] = {}
LINK_TO_ORDER: dict[str, str] = {}
PURCHASE_TO_ORDER: dict[str, str] = {}  # Cybersource purchaseNumber -> order, for webhooks
# "decision_id:merchant" -> order_id ("" while the payment link is being made). One order per decision per store:
# a decision over several stores is placed as one signed order at each. Kept across /reset, like the nonces, so
# one policy decision can never buy twice at the same store.
DECISION_TO_ORDER: dict[str, str] = {}
TIMERS: dict[str, set[asyncio.Task]] = {}  # order_id -> lifecycle and refund timers, cancelled on /reset


@asynccontextmanager
async def lifespan(app: FastAPI):
    await storefront_links.start()
    yield
    await storefront_links.close()


app = FastAPI(title="Chaperone merchants", lifespan=lifespan)


class CartLine(BaseModel):
    sku: str
    qty: int = Field(ge=1, le=MAX_QTY)
    confidence: float | None = None
    account_ref: str | None = Field(None, max_length=40)  # a bill line's account, when policy names it


class Cart(BaseModel):
    items: list[CartLine] = Field(min_length=1)
    merchant: str | None = None  # the storefront policy decided for; absent means corner_market


class CancelRequest(BaseModel):
    mandate_id: str
    decision_id: str
    order_id: str
    session_id: str | None = None


class RefundRequest(BaseModel):
    """No destination: money only goes back to the card that paid. amount, if sent, must equal our own price."""
    mandate_id: str
    decision_id: str
    order_id: str
    sku: str
    qty: int = Field(ge=1, le=MAX_QTY)
    amount: str | None = None
    reason: str | None = Field(None, max_length=200)
    session_id: str | None = None


class OrderRequest(BaseModel):
    mandate_id: str
    decision_id: str
    cart: Cart
    approval_id: str | None = None
    session_id: str | None = None
    lang: str | None = None


@app.get("/health")
def health():
    return {"ok": True, "backend": payment_links.backend,
            "storefronts": {s["merchant"]: s["backend"] for s in storefront_links.describe()}}


async def _mandate_account_ref(biller_id: str, mandate_id: str | None) -> str | None:
    """Ruth's account at this biller, from the mandate policy checks (GET /mandate billers[]). None -> default."""
    base = os.environ.get("POLICY_URL", "http://127.0.0.1:8001").rstrip("/")
    try:
        async with httpx.AsyncClient(verify=tls.context(), timeout=0.5) as client:
            r = await client.get(f"{base}/mandate")
        body = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    mandate = body.get("mandate") if isinstance(body.get("mandate"), dict) else body
    if mandate_id and mandate.get("mandate_id") not in (None, mandate_id):
        return None
    for row in mandate.get("billers") or []:
        if isinstance(row, dict) and row.get("merchant_id") == biller_id and row.get("account_ref"):
            return str(row["account_ref"])
    return None


def sku_merchant(sku: str) -> str | None:
    """Which store sells a sku: BILL-<biller> is that biller's, anything else is the catalog's."""
    if biller.is_bill(sku):
        return sku[len(biller.BILL_SKU_PREFIX):]
    item = catalog.items.get(sku)
    return item.get("merchant", merchants.DEFAULT) if item else None


def _body_merchant(body: bytes) -> str:
    """The storefront named in the signed body, read before verification only to keep its nonces apart."""
    try:
        merchant = json.loads(body)["cart"].get("merchant")
    except (ValueError, KeyError, TypeError, AttributeError):
        merchant = None
    return merchant if isinstance(merchant, str) and merchant else merchants.DEFAULT


@app.post("/orders")
async def create_order(request: Request):
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    merchant = _body_merchant(body)
    verification = await verify_request(request.method, request.url.netloc, request.url.path, headers, body,
                                        merchant=merchant, sku_merchant=sku_merchant)
    if not verification.ok:
        await events.emit("signature_rejected", checks=verification.checks, merchant=merchant)
        raise HTTPException(401, {"error": "signature rejected", "checks": verification.checks})

    try:
        order_req = OrderRequest.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    ids = {"session_id": order_req.session_id, "mandate_id": order_req.mandate_id, "decision_id": order_req.decision_id}
    entry = merchants.get(merchant)
    if entry is None or entry["kind"] not in merchants.BUYABLE_KINDS:
        await events.emit("signature_rejected", checks=[*verification.checks, {
            "id": "storefront", "passed": False,
            "detail": f"{merchant} is {'not a known store' if entry is None else 'blocked'}"}], merchant=merchant, **ids)
        raise HTTPException(403, {"error": "not a storefront the agent may buy from", "merchant": merchant})
    claim = f"{order_req.decision_id}:{merchant}"
    if claim in DECISION_TO_ORDER:
        await events.emit("signature_rejected", checks=[*verification.checks[:-1], {
            "id": "decision", "passed": False,
            "detail": f"decision {order_req.decision_id} already has order {DECISION_TO_ORDER[claim] or '(in progress)'}"
                      f" at {entry['name']}"}],
            **ids)
        raise HTTPException(409, {"error": "decision already used", "order_id": DECISION_TO_ORDER[claim],
                                  "merchant": merchant})
    DECISION_TO_ORDER[claim] = ""  # claimed before the first await, so two racing requests cannot both pass
    try:
        return await _place_order(order_req, verification, ids, entry)
    except BaseException:
        if not DECISION_TO_ORDER.get(claim):
            DECISION_TO_ORDER.pop(claim, None)  # nothing was made: the decision may try again
        raise


async def _place_order(order_req: OrderRequest, verification, ids: dict, entry: dict) -> dict:
    merchant, store = entry["id"], entry["name"]
    await events.emit("signature_verified", keyid=verification.keyid, checks=verification.checks,
                      merchant=merchant, store=store, **verification.params, **ids)

    # Price from our own catalog; the agent only says which SKUs and how many.
    lines, total_cents = [], 0
    for line in order_req.cart.items:
        if biller.is_bill(line.sku):
            try:
                ref = line.account_ref or await _mandate_account_ref(merchant, order_req.mandate_id)
                bill_line = biller.price_line(line.sku, merchant, line.qty, ref)
            except biller.BillError as e:
                raise HTTPException(e.http_status, e.error) from e
            total_cents += aftercare.cents(bill_line["unit_price"])
            lines.append(bill_line)
            continue
        item = catalog.items.get(line.sku)
        if item is None:
            raise HTTPException(422, f"unknown sku {line.sku}")
        cents = round(item["price"] * 100)
        total_cents += cents * line.qty
        regular = item.get("regular_price")
        lines.append({"sku": line.sku, "name": item["name"], "qty": line.qty, "unit_price": money(cents / 100),
                      "merchant": item.get("merchant", merchants.DEFAULT),
                      "regular_price": money(regular) if regular and regular > item["price"] else None,
                      "category": item["category"], "mandate_category": item.get("mandate_category")})
    amount = money(total_cents / 100)

    try:
        link = await storefront_links.links(merchant).create(
            purchase_number=storefront_links.purchase_number(merchant),
            amount=amount,
            currency="USD",
            line_items=[LineItem(productName=l["name"], quantity=l["qty"], unitPrice=l["unit_price"], productSKU=l["sku"])
                        for l in lines],
            store=store,
        )
    except Exception as e:  # noqa: BLE001 - only reachable with VISA_FALLBACK_TO_MOCK=0
        raise HTTPException(502, f"payment link failed: {e}") from e
    order_id = "ord_" + secrets.token_hex(6)
    order = {
        "order_id": order_id,
        **ids,
        "merchant": merchant,
        "store": store,
        "approval_id": order_req.approval_id,
        "lang": order_req.lang if order_req.lang in RECEIPT_LANGS else None,
        "lines": lines,
        "amount": amount,
        "currency": "USD",
        "status": "awaiting_payment",
        "payment_link": {"id": link.id, "url": link.url, "backend": link.backend, "purchase_number": link.purchase_number,
                         "line_item": link.line_item},
        "checkout_url": f"{PUBLIC_URL}/checkout/{order_id}" if CARD_AUTH else None,
        "card_auth": None,
        "verification": verification.checks,
        "created_at": time.time(),
        "paid_at": None,
        "pickup_code": None if entry["kind"] == "biller" else aftercare.new_pickup_code(),
        "fulfilment": None,
        "timeline": [],
        "refunds": [],
        "cancel": None,
        "card_last4": None,
        "savings": None,
        "loyalty_points": aftercare.loyalty_points(amount) if merchant == merchants.DEFAULT else None,
        # Visa risk score (Decision Manager) on the store's own account: pending until it answers; None when
        # the link is a mock (no Visa account in play)
        "risk": {"status": "pending"} if link.backend == "visa" else None,
    }
    saved = aftercare.savings_cents(lines)
    order["savings"] = money(saved / 100) if saved else None  # never shown when nothing was saved
    aftercare.record(order, "awaiting_payment")
    ORDERS[order_id] = order
    DECISION_TO_ORDER[f"{order_req.decision_id}:{merchant}"] = order_id
    LINK_TO_ORDER[link.id] = order_id
    PURCHASE_TO_ORDER[link.purchase_number] = order_id
    await events.emit("payment_link_created", order_id=order_id, amount=amount, link_id=link.id, url=link.url,
                      backend=link.backend, merchant=merchant, store=store, purchase_number=link.purchase_number, **ids)
    if order["risk"] is not None:
        _start(order_id, _risk_score(order_id), "risk")
    return order


async def _risk_score(order_id: str) -> None:
    order = ORDERS.get(order_id)
    if order is None:
        return
    answer = await risk.score(order)
    order["risk"] = answer
    await events.emit("risk_scored", order_id=order_id, merchant=order["merchant"], store=order["store"],
                      status=answer["status"], score=answer["score"], ms=answer["ms"], error=answer["error"],
                      risk_id=answer["id"], **_ids(order))


@app.get("/orders")
def list_orders(session_id: str | None = None):
    found = [o for o in ORDERS.values() if session_id is None or o["session_id"] == session_id]
    return sorted(found, key=lambda o: o["created_at"], reverse=True)


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    if order_id not in ORDERS:
        raise HTTPException(404, "unknown order")
    return ORDERS[order_id]


def session_url(session_id: str | None) -> str | None:
    """The public session page behind the receipt's QR code: the caregiver app's /s/<id> through the tunnel."""
    if not session_id or session_id == "none":
        return None
    tunnel = os.environ.get("TUNNEL_HOST", "").strip().split("://")[-1].strip("/")
    if tunnel:
        return f"https://{tunnel}/s/{session_id}"
    # No public host here: leave it empty so the station fills in its own tunnel URL. A LAN address would
    # print a QR code that phones on mobile data cannot open.
    return None


@app.get("/orders/{order_id}/receipt")
def receipt(order_id: str, lang: str | None = None):
    order = get_order(order_id)
    paid_at = order["paid_at"]
    return {
        "merchant": order.get("store") or merchants.name(merchants.DEFAULT),
        "store": order.get("store") or merchants.name(merchants.DEFAULT),
        "merchant_id": order.get("merchant") or merchants.DEFAULT,
        "kind": (merchants.get(order.get("merchant")) or {}).get("kind", "store"),
        "items": [{"name": l["name"], "qty": l["qty"], "price": money(float(l["unit_price"]) * l["qty"]),
                   "unit_price": l["unit_price"], "sku": l["sku"]} for l in order["lines"]],
        "total": order["amount"],
        "currency": order["currency"],
        "pickup": "after 3pm" if order["pickup_code"] else None,
        "order_id": order_id,
        "decision_id": order["decision_id"],
        "session_id": order["session_id"],
        "status": order["status"],
        "paid_at": datetime.datetime.fromtimestamp(paid_at, datetime.timezone.utc).isoformat() if paid_at else None,
        "paid_via": order.get("paid_via"),
        "session_url": session_url(order["session_id"]),
        "lang": lang if lang in RECEIPT_LANGS else order.get("lang") or "en",
        "sandbox_note": "Paid in the Visa sandbox. No real money.",
        "pickup_code": order["pickup_code"],
        "savings": order["savings"],
        "loyalty_points": order["loyalty_points"] if order["paid_at"] else None,
        "loyalty_program": aftercare.LOYALTY_PROGRAM if order["loyalty_points"] is not None else None,
        "card_last4": order["card_last4"],
        "timeline": order["timeline"],
        "refunds": [{k: r[k] for k in ("refund_id", "sku", "name", "qty", "amount", "status", "label")}
                    for r in order["refunds"]],
    }


async def mark_paid(order_id: str, via: str) -> dict:
    """Only an order waiting for payment becomes paid: a repeat, a later step or a cancelled order stays as it is."""
    order = ORDERS[order_id]
    if order["status"] != "awaiting_payment":
        return order
    order["paid_at"], order["paid_via"] = time.time(), via
    auth = order.get("card_auth") or {}
    order["card_last4"] = auth.get("card_last4") or aftercare.SANDBOX_CARD_LAST4
    aftercare.record(order, "paid", via=via)
    await events.emit("paid", order_id=order_id, total=order["amount"], amount=order["amount"], via=via,
                      merchant=order.get("merchant"), store=order.get("store"), session_id=order["session_id"],
                      mandate_id=order["mandate_id"], decision_id=order["decision_id"])
    biller.record_payment(order["lines"], order["paid_at"])
    if order["pickup_code"]:  # a bill has nothing to prepare or pick up
        _start(order_id, _fulfil(order_id), "fulfil")
    return order


def _start(order_id: str, coro, name: str) -> None:
    task = asyncio.create_task(coro, name=name)
    TIMERS.setdefault(order_id, set()).add(task)
    task.add_done_callback(lambda t: TIMERS.get(order_id, set()).discard(t))


def _ids(order: dict) -> dict:
    return {"session_id": order["session_id"], "mandate_id": order["mandate_id"], "decision_id": order["decision_id"]}


async def _advance(order_id: str, status: str) -> None:
    order = ORDERS.get(order_id)
    if order is None or order["status"] == "refunded":
        return
    aftercare.record(order, status)
    await events.emit("order_status", order_id=order_id, status=status, pickup_code=order["pickup_code"],
                      **_ids(order))


async def _fulfil(order_id: str) -> None:
    await asyncio.sleep(aftercare.preparing_after())
    await _advance(order_id, "preparing")
    await asyncio.sleep(max(0.0, aftercare.ready_after() - aftercare.preparing_after()))
    await _advance(order_id, "ready_for_pickup")


async def _verified_action(request: Request, order_id: str, model):
    """Post-purchase requests carry the same RFC 9421 signature and five checks as an order."""
    order = ORDERS.get(order_id)
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    verification = await verify_request(request.method, request.url.netloc, request.url.path, headers, body,
                                        existing_order=order,
                                        merchant=(order or {}).get("merchant") or merchants.DEFAULT)
    if not verification.ok:
        await events.emit("signature_rejected", checks=verification.checks, **(_ids(order) if order else {}))
        raise HTTPException(401, {"error": "signature rejected", "checks": verification.checks})
    try:
        action = model.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    if order is None:
        raise HTTPException(404, "unknown order")
    if action.order_id != order_id:
        raise HTTPException(422, "order_id in the body differs from the path")
    if action.mandate_id != order["mandate_id"]:
        raise HTTPException(403, "order belongs to another mandate")
    await events.emit("signature_verified", keyid=verification.keyid, checks=verification.checks,
                      action=model.__name__.removesuffix("Request").lower(), order_id=order_id,
                      **verification.params, **{**_ids(order), "decision_id": action.decision_id})
    return order, action


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, request: Request):
    order, action = await _verified_action(request, order_id, CancelRequest)
    if order["status"] == "cancelled":
        return {"status": "cancelled", "link_status": order["cancel"]["link_status"], "duplicate": True}
    if order["status"] != "awaiting_payment":
        raise HTTPException(409, {"error": "only an unpaid order can be cancelled; a paid one can be returned",
                                  "status": order["status"]})
    link = order["payment_link"]
    try:
        result = await storefront_links.links(order.get("merchant")).deactivate(
            link["id"], order["amount"], link.get("line_item") or {})
    except Exception as e:  # noqa: BLE001 - the order stays payable and unchanged if the link could not be closed
        raise HTTPException(502, f"could not deactivate the payment link: {e}") from e
    if order["status"] != "awaiting_payment":  # paid while the PATCH was in flight
        raise HTTPException(409, {"error": "the order was paid while cancelling", "status": order["status"]})
    order["cancel"] = {**result, "at": aftercare.iso(time.time()), "decision_id": action.decision_id}
    aftercare.record(order, "cancelled", link_status=result["link_status"])
    await events.emit("order_cancelled", order_id=order_id, amount=order["amount"], link_status=result["link_status"],
                      request_id=result.get("request_id"), backend=result.get("backend"), **_ids(order))
    return {"status": "cancelled", "order_id": order_id, "amount": order["amount"], **result}


@app.post("/orders/{order_id}/refunds")
async def refund_order(order_id: str, request: Request):
    order, action = await _verified_action(request, order_id, RefundRequest)
    try:
        line, amount_cents = aftercare.check_refund(order, action.sku, action.qty, action.amount)
    except aftercare.RefundRefused as e:
        raise HTTPException(e.http_status, {"error": e.error, **e.detail}) from e
    answer = aftercare.refund_response(order, amount_cents)
    refund = {"refund_id": answer["id"], "sku": line["sku"], "name": line["name"], "qty": action.qty,
              "amount": aftercare.dollars(amount_cents), "status": "PENDING",
              "reconciliation_id": answer["reconciliationId"], "decision_id": action.decision_id,
              "reason": action.reason, "card_last4": order["card_last4"], "label": aftercare.STUB_LABEL,
              "at": aftercare.iso(time.time())}
    order["refunds"].append(refund)
    left = aftercare.cents(order["amount"]) - aftercare.refunded_cents(order)
    if order["loyalty_points"] is not None:  # points follow the money that stayed paid
        order["loyalty_points"] = aftercare.loyalty_points(aftercare.dollars(left))
    aftercare.record(order, "refunded" if left == 0 else "partially_refunded", refund_id=refund["refund_id"],
                     amount=refund["amount"])
    await _refund_event(order, refund)
    _start(order_id, _transmit(order_id, refund["refund_id"]), "refund")
    return answer


async def _refund_event(order: dict, refund: dict) -> None:
    await events.emit("refund_result", order_id=order["order_id"], refund_id=refund["refund_id"],
                      status=refund["status"], amount=refund["amount"], sku=refund["sku"], qty=refund["qty"],
                      reconciliation_id=refund["reconciliation_id"], card_last4=refund["card_last4"],
                      label=refund["label"], **{**_ids(order), "decision_id": refund["decision_id"]})


async def _transmit(order_id: str, refund_id: str) -> None:
    await asyncio.sleep(aftercare.transmit_after())
    order = ORDERS.get(order_id)
    refund = next((r for r in (order or {}).get("refunds", []) if r["refund_id"] == refund_id), None)
    if refund and refund["status"] == "PENDING":
        refund["status"] = "TRANSMITTED"
        await _refund_event(order, refund)


@app.post("/orders/{order_id}/picked-up")
async def picked_up(order_id: str, request: Request):
    host_header.require(request)
    order = get_order(order_id)
    if order.get("fulfilment") not in ("paid", "preparing", "ready_for_pickup"):
        raise HTTPException(409, {"error": "nothing to pick up", "status": order["status"]})
    for task in list(TIMERS.get(order_id, set())):
        if task.get_name() == "fulfil":
            task.cancel()
    await _advance(order_id, "picked_up")
    return order


@app.post("/orders/{order_id}/paid")
async def paid_callback(order_id: str, request: Request, via: str = "callback"):
    host_header.require(request)  # the Host's Confirm payment; Cybersource uses the signed webhook
    if order_id not in ORDERS:
        raise HTTPException(404, "unknown order")
    return await mark_paid(order_id, via)


@app.get("/pay/{link_id}", response_class=HTMLResponse)
def mock_pay_page(link_id: str):
    order_id = LINK_TO_ORDER.get(link_id)
    if order_id is None:
        raise HTTPException(404, "unknown payment link")
    return HTMLResponse(render_pay_page(ORDERS[order_id]))


@app.post("/pay/{link_id}", response_class=HTMLResponse)
async def mock_pay_submit(link_id: str):
    order_id = LINK_TO_ORDER.get(link_id)
    if order_id is None:
        raise HTTPException(404, "unknown payment link")
    return HTMLResponse(render_pay_page(await mark_paid(order_id, via="mock_hosted_page")))


def _checkout_order(order_id: str) -> dict:
    if not CARD_AUTH:
        raise HTTPException(404, "card authorization checkout is off (set CARD_AUTH=1)")
    if order_id not in ORDERS:
        raise HTTPException(404, "unknown order")
    return ORDERS[order_id]


@app.get("/checkout/{order_id}", response_class=HTMLResponse)
def checkout_page(order_id: str):
    return HTMLResponse(render_checkout_page(_checkout_order(order_id)))


@app.post("/checkout/{order_id}", response_class=HTMLResponse)
async def checkout_submit(order_id: str, request: Request):
    order = _checkout_order(order_id)
    if order["paid_at"] or order["status"] == "cancelled":  # a double click never authorizes twice
        return HTMLResponse(render_checkout_page(order))
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    ids = {"session_id": order["session_id"], "mandate_id": order["mandate_id"], "decision_id": order["decision_id"]}
    result = await asyncio.to_thread(
        card_auth.authorize, order["amount"], order["order_id"], form.get("number", ""),
        form.get("exp_month", ""), form.get("exp_year", ""), form.get("cvv", ""),
    )
    order["card_auth"] = result.to_dict()
    if result.ok:
        await events.emit("card_authorized", order_id=order_id, request_id=result.request_id,
                          approval_code=result.approval_code, merchant=result.merchant, **ids)
        await mark_paid(order_id, via=f"card_auth:{result.merchant}")
    else:  # one attempt per click, never retried automatically
        await events.emit("card_auth_failed", order_id=order_id, status=result.status, reason=result.reason,
                          request_id=result.request_id, merchant=result.merchant, **ids)
    return HTMLResponse(render_checkout_page(order))


@app.get("/billers/{biller_id}/accounts/{account_ref}")
async def bill_account(biller_id: str, account_ref: str, lang: str = "en", session_id: str | None = None,
                       purpose: str = "status"):
    """The real balance. Read-only. bill_status and the scam check ask about the bill (posts bill_checked);
    policy's price lookup sends purpose=price and posts nothing."""
    try:
        facts = biller.account(biller_id, account_ref, lang)
    except biller.BillError as e:
        raise HTTPException(e.http_status, e.error) from e
    if purpose == "price":
        return facts
    await events.emit("bill_checked", biller=facts["biller"], account_ref=facts["account_ref"],
                      balance_due=facts["balance_due"], due_date=facts["due_date"], past_due=facts["past_due"],
                      session_id=session_id)
    return facts


@app.get("/panel")
def panel():
    return {
        "merchant": "Corner Market",
        "storefronts": storefront_links.describe(),
        "backend": payment_links.backend,
        "visa_last_error": getattr(payment_links, "last_error", None),
        "orders": list_orders()[:10],
        "events": list(events.RECENT)[-50:],
    }


@app.post("/webhooks/cybersource")
async def cybersource_webhook(request: Request):
    """Pay by Link payment notification. HMAC over the exact bytes received; nothing is re-parsed first."""
    raw = await request.body()
    print(f"[webhook] {request.headers.get('v-c-event-type', '?')} {raw[:2000]!r}")
    try:
        note = webhooks.verify(raw, request.headers.get("v-c-signature"))
    except webhooks.WebhookError as e:
        status = 503 if "not configured" in str(e) else 401
        await events.emit("signature_rejected", checks=[{"id": "cybersource_webhook", "passed": False,
                                                         "detail": str(e)}])
        raise HTTPException(status, str(e)) from e
    # Only the signed part is searched and read: an unsigned envelope must not steer which order is paid.
    found = [s for s in webhooks.strings_in(note.signed) if s in PURCHASE_TO_ORDER or s in LINK_TO_ORDER]
    if not found:  # 200 so Cybersource does not retry a notification for an order we never made
        return {"ok": True, "matched": False}
    order_ids = {PURCHASE_TO_ORDER.get(s) or LINK_TO_ORDER[s] for s in found}
    if len(order_ids) > 1:
        return {"ok": True, "matched": False, "ignored": "names more than one order"}
    order_id = order_ids.pop()
    if note.merchant and ORDERS[order_id].get("merchant") != note.merchant:  # one store's key pays only its orders
        return {"ok": True, "matched": False, "ignored": f"signed by {note.merchant}'s key"}
    paid, what = webhooks.is_payment(note.signed)
    if not paid:
        return {"ok": True, "matched": True, "ignored": what}
    order = ORDERS[order_id]
    duplicate = note.duplicate or bool(order["paid_at"])
    if not duplicate:
        order = await mark_paid(order_id, via=f"cybersource_webhook ({note.variant})")
    return {"ok": True, "matched": True, "order_id": order_id, "status": order["status"], "duplicate": duplicate}


@app.api_route("/webhooks/cybersource/health", methods=["GET", "POST"])
def cybersource_webhook_health():
    """Cybersource's subscription health check calls this with both methods."""
    return {"ok": True}


@app.post("/reset")
def reset(request: Request):
    """Nonces stay: a reset must not let an old signed order replay."""
    host_header.require(request)
    cleared = len(ORDERS)
    for tasks in TIMERS.values():
        for task in tasks:
            task.cancel()
    TIMERS.clear()
    ORDERS.clear()
    LINK_TO_ORDER.clear()
    PURCHASE_TO_ORDER.clear()
    biller.reset()
    events.clear()
    return {"ok": True, "orders_cleared": cleared}


def render_checkout_page(order: dict) -> str:
    auth = order.get("card_auth") or {}
    if order["paid_at"]:
        detail = ""
        if auth.get("ok"):
            detail = (f"<p class=\"note\">Authorized by the Cybersource sandbox · request {escape(auth['request_id'] or '')}"
                      f"{' · approval ' + escape(auth['approval_code']) if auth.get('approval_code') else ''}</p>")
        action = '<p class="paid">Paid. Thank you.</p>' + detail
    else:
        error = ""
        if auth and not auth.get("ok"):
            hint = ("Payment system error. Do not retry in a loop: the Host can mark the order paid."
                    if auth.get("status") == "SERVER_ERROR" else "Please check the card details.")
            error = (f"<p class=\"err\">{escape(auth.get('status') or '')}: {escape(auth.get('reason') or '')}"
                     f"<br>{hint}</p>")
        action = (error + '<form method="post">'
                  '<label>Card number<input name="number" value="4111 1111 1111 1111" inputmode="numeric"></label>'
                  '<div class="row"><label>Month<input name="exp_month" value="12"></label>'
                  '<label>Year<input name="exp_year" value="2030"></label>'
                  '<label>CVV<input name="cvv" value="123"></label></div>'
                  f'<button>Pay ${order["amount"]}</button></form>'
                  '<p class="note">Cybersource sandbox · test cards only · no real money moves.</p>')
    page = render_pay_page(order)
    start = page.index("<table>")
    end = page.index("</main>")
    table_end = page.index("</table>", start) + len("</table>")
    extra_css = ("label{display:block;margin-top:12px;font-size:16px;color:#333}"
                 "input{display:block;width:100%;box-sizing:border-box;font-size:22px;padding:12px;margin-top:4px;"
                 "border:1px solid #ccc;border-radius:10px}.row{display:flex;gap:10px}.row label{flex:1}"
                 ".err{margin-top:16px;padding:14px;border-radius:12px;background:#fdecea;color:#8a1c12;font-size:17px}")
    return page[:table_end].replace("</style>", extra_css + "</style>") + action + page[end:]


def render_pay_page(order: dict) -> str:
    store = order.get("store") or "Corner Market"
    rows = "".join(
        f"<tr><td>{l['qty']} × {escape(l['name'])}</td><td>${l['unit_price']}</td></tr>" for l in order["lines"]
    )
    if order["paid_at"]:
        action = '<p class="paid">Paid. Thank you.</p>'
    elif order["status"] == "cancelled":
        action = '<p class="note">This order was cancelled. Nothing to pay.</p>'
    else:
        action = ('<form method="post"><button>Pay $' + order["amount"] + ' with Visa •••• 1111</button></form>'
                  '<p class="note">Sandbox test card. No real money moves.</p>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(store)} checkout</title><style>
body{{font:20px/1.4 system-ui,sans-serif;margin:0;padding:24px;background:#f6f7fb;color:#111}}
main{{max-width:520px;margin:auto;background:#fff;border-radius:16px;padding:24px;box-shadow:0 2px 12px #0001}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#555;margin:0 0 16px;font-size:16px}}
table{{width:100%;border-collapse:collapse}} td{{padding:8px 0;border-bottom:1px solid #eee}} td:last-child{{text-align:right}}
.total td{{font-weight:700;border:0;padding-top:12px}}
button{{width:100%;margin-top:20px;padding:18px;font-size:22px;border:0;border-radius:12px;background:#1a1f71;color:#fff;cursor:pointer}}
.paid{{margin-top:20px;padding:18px;border-radius:12px;background:#e6f6ea;color:#0a6b2b;font-weight:700;text-align:center;font-size:22px}}
.note{{color:#666;font-size:14px;text-align:center}}
</style></head><body><main>
<h1>{escape(store)}</h1><p class="sub">Order {escape(order['order_id'])} · decision {escape(order['decision_id'])}</p>
<table>{rows}<tr class="total"><td>Total</td><td>${order['amount']}</td></tr></table>
{action}</main></body></html>"""
