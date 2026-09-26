"""Corner Market merchant (port 8002).

    python -m uvicorn merchant.orders:app --host 0.0.0.0 --port 8002

POST /orders             signed order from the checkout tool -> verify -> price from catalog -> payment link
                         (409 if the decision already has an order)
GET  /orders[/{id}]      order state (wall, receipt)
POST /orders/{id}/paid   callback fallback: Visa webhook or the Host marks paid
GET  /pay/{link_id}      mock hosted payment page (MOCK_VISA=1 or fallback)
GET  /checkout/{id}      CARD_AUTH=1: our checkout page with a real Cybersource sandbox card authorization
GET  /panel              merchant verification panel data for the wall
POST /reset              demo reset: clear orders and the panel's event buffer (nonces are kept)
POST /webhooks/cybersource            signed Cybersource webhook -> order paid (see merchant.webhooks)
"""

import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from catalog.search import Catalog  # noqa: E402
from merchant import card_auth, events, webhooks  # noqa: E402
from merchant.verify import verify_request  # noqa: E402
from merchant.visa import LineItem, get_payment_links, money, new_purchase_number  # noqa: E402

PUBLIC_URL = os.environ.get("MERCHANT_PUBLIC_URL", "http://192.168.8.10:8002")
MAX_QTY = 24
# CARD_AUTH=1: orders also get our own checkout page that runs a real sandbox card authorization
# (merchant.card_auth) and marks the order paid only on AUTHORIZED. Default off: behavior unchanged.
CARD_AUTH = os.environ.get("CARD_AUTH") == "1"

catalog = Catalog.load()
payment_links = get_payment_links(PUBLIC_URL)
ORDERS: dict[str, dict] = {}
LINK_TO_ORDER: dict[str, str] = {}
PURCHASE_TO_ORDER: dict[str, str] = {}  # Cybersource purchaseNumber -> order, for webhooks
# decision_id -> order_id ("" while the payment link is being made). Kept across /reset, like the nonces,
# so one policy decision can never buy twice.
DECISION_TO_ORDER: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await payment_links.start()
    yield
    await payment_links.close()


app = FastAPI(title="Corner Market", lifespan=lifespan)


class CartLine(BaseModel):
    sku: str
    qty: int = Field(ge=1, le=MAX_QTY)
    confidence: float | None = None


class Cart(BaseModel):
    items: list[CartLine] = Field(min_length=1)


class OrderRequest(BaseModel):
    mandate_id: str
    decision_id: str
    cart: Cart
    approval_id: str | None = None
    session_id: str | None = None


@app.get("/health")
def health():
    return {"ok": True, "backend": payment_links.backend}


@app.post("/orders")
async def create_order(request: Request):
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    verification = await verify_request(request.method, request.url.netloc, request.url.path, headers, body)
    if not verification.ok:
        await events.emit("signature_rejected", checks=verification.checks)
        raise HTTPException(401, {"error": "signature rejected", "checks": verification.checks})

    try:
        order_req = OrderRequest.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    ids = {"session_id": order_req.session_id, "mandate_id": order_req.mandate_id, "decision_id": order_req.decision_id}
    if order_req.decision_id in DECISION_TO_ORDER:
        await events.emit("signature_rejected", checks=[*verification.checks[:-1], {
            "id": "decision", "passed": False,
            "detail": f"decision {order_req.decision_id} already has order {DECISION_TO_ORDER[order_req.decision_id] or '(in progress)'}"}],
            **ids)
        raise HTTPException(409, {"error": "decision already used", "order_id": DECISION_TO_ORDER[order_req.decision_id]})
    DECISION_TO_ORDER[order_req.decision_id] = ""  # claimed before the first await, so two racing requests cannot both pass
    try:
        return await _place_order(order_req, verification, ids)
    except BaseException:
        if not DECISION_TO_ORDER.get(order_req.decision_id):
            DECISION_TO_ORDER.pop(order_req.decision_id, None)  # nothing was made: the decision may try again
        raise


async def _place_order(order_req: OrderRequest, verification, ids: dict) -> dict:
    await events.emit("signature_verified", keyid=verification.keyid, checks=verification.checks,
                      **verification.params, **ids)

    # Price from our own catalog; the agent only says which SKUs and how many.
    lines, total_cents = [], 0
    for line in order_req.cart.items:
        item = catalog.items.get(line.sku)
        if item is None:
            raise HTTPException(422, f"unknown sku {line.sku}")
        cents = round(item["price"] * 100)
        total_cents += cents * line.qty
        lines.append({"sku": line.sku, "name": item["name"], "qty": line.qty, "unit_price": money(cents / 100),
                      "category": item["category"], "mandate_category": item.get("mandate_category")})
    amount = money(total_cents / 100)

    try:
        link = await payment_links.create(
            purchase_number=new_purchase_number(),
            amount=amount,
            currency="USD",
            line_items=[LineItem(productName=l["name"], quantity=l["qty"], unitPrice=l["unit_price"], productSKU=l["sku"])
                        for l in lines],
        )
    except Exception as e:  # noqa: BLE001 - only reachable with VISA_FALLBACK_TO_MOCK=0
        raise HTTPException(502, f"payment link failed: {e}") from e
    order_id = "ord_" + secrets.token_hex(6)
    order = {
        "order_id": order_id,
        **ids,
        "approval_id": order_req.approval_id,
        "lines": lines,
        "amount": amount,
        "currency": "USD",
        "status": "awaiting_payment",
        "payment_link": {"id": link.id, "url": link.url, "backend": link.backend, "purchase_number": link.purchase_number},
        "checkout_url": f"{PUBLIC_URL}/checkout/{order_id}" if CARD_AUTH else None,
        "card_auth": None,
        "verification": verification.checks,
        "created_at": time.time(),
        "paid_at": None,
    }
    ORDERS[order_id] = order
    DECISION_TO_ORDER[order_req.decision_id] = order_id
    LINK_TO_ORDER[link.id] = order_id
    PURCHASE_TO_ORDER[link.purchase_number] = order_id
    await events.emit("payment_link_created", order_id=order_id, amount=amount, link_id=link.id, url=link.url,
                      backend=link.backend, **ids)
    return order


@app.get("/orders")
def list_orders():
    return sorted(ORDERS.values(), key=lambda o: o["created_at"], reverse=True)


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    if order_id not in ORDERS:
        raise HTTPException(404, "unknown order")
    return ORDERS[order_id]


async def mark_paid(order_id: str, via: str) -> dict:
    order = ORDERS[order_id]
    if order["status"] != "paid":
        order["status"], order["paid_at"], order["paid_via"] = "paid", time.time(), via
        await events.emit("paid", order_id=order_id, total=order["amount"], amount=order["amount"], via=via,
                          session_id=order["session_id"],
                          mandate_id=order["mandate_id"], decision_id=order["decision_id"])
    return order


@app.post("/orders/{order_id}/paid")
async def paid_callback(order_id: str, via: str = "callback"):
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
    if order["status"] == "paid":  # a double click never authorizes twice
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


@app.get("/panel")
def panel():
    return {
        "merchant": "Corner Market",
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
    paid, what = webhooks.is_payment(note.signed)
    if not paid:
        return {"ok": True, "matched": True, "ignored": what}
    order = ORDERS[order_id]
    duplicate = note.duplicate or order["status"] == "paid"
    if not duplicate:
        order = await mark_paid(order_id, via=f"cybersource_webhook ({note.variant})")
    return {"ok": True, "matched": True, "order_id": order_id, "status": order["status"], "duplicate": duplicate}


@app.api_route("/webhooks/cybersource/health", methods=["GET", "POST"])
def cybersource_webhook_health():
    """Cybersource's subscription health check calls this with both methods."""
    return {"ok": True}


@app.post("/reset")
def reset():
    """Nonces stay: a reset must not let an old signed order replay."""
    cleared = len(ORDERS)
    ORDERS.clear()
    LINK_TO_ORDER.clear()
    PURCHASE_TO_ORDER.clear()
    events.clear()
    return {"ok": True, "orders_cleared": cleared}


def render_checkout_page(order: dict) -> str:
    auth = order.get("card_auth") or {}
    if order["status"] == "paid":
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
    rows = "".join(
        f"<tr><td>{l['qty']} × {escape(l['name'])}</td><td>${l['unit_price']}</td></tr>" for l in order["lines"]
    )
    if order["status"] == "paid":
        action = '<p class="paid">Paid. Thank you.</p>'
    else:
        action = ('<form method="post"><button>Pay $' + order["amount"] + ' with Visa •••• 1111</button></form>'
                  '<p class="note">Sandbox test card. No real money moves.</p>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Corner Market checkout</title><style>
body{{font:20px/1.4 system-ui,sans-serif;margin:0;padding:24px;background:#f6f7fb;color:#111}}
main{{max-width:520px;margin:auto;background:#fff;border-radius:16px;padding:24px;box-shadow:0 2px 12px #0001}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#555;margin:0 0 16px;font-size:16px}}
table{{width:100%;border-collapse:collapse}} td{{padding:8px 0;border-bottom:1px solid #eee}} td:last-child{{text-align:right}}
.total td{{font-weight:700;border:0;padding-top:12px}}
button{{width:100%;margin-top:20px;padding:18px;font-size:22px;border:0;border-radius:12px;background:#1a1f71;color:#fff;cursor:pointer}}
.paid{{margin-top:20px;padding:18px;border-radius:12px;background:#e6f6ea;color:#0a6b2b;font-weight:700;text-align:center;font-size:22px}}
.note{{color:#666;font-size:14px;text-align:center}}
</style></head><body><main>
<h1>Corner Market</h1><p class="sub">Order {escape(order['order_id'])} · decision {escape(order['decision_id'])}</p>
<table>{rows}<tr class="total"><td>Total</td><td>${order['amount']}</td></tr></table>
{action}</main></body></html>"""
