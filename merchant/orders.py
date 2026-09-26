"""Corner Market merchant (port 8002).

    python -m uvicorn merchant.orders:app --host 0.0.0.0 --port 8002

POST /orders             signed order from the checkout tool -> verify -> price from catalog -> payment link
GET  /orders[/{id}]      order state (wall, receipt)
POST /orders/{id}/paid   callback fallback: Visa webhook or the Host marks paid
GET  /pay/{link_id}      mock hosted payment page (MOCK_VISA=1 or fallback)
GET  /panel              merchant verification panel data for the wall
"""

import os
import secrets
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from catalog.search import Catalog  # noqa: E402
from merchant import events  # noqa: E402
from merchant.verify import verify_request  # noqa: E402
from merchant.visa import LineItem, get_payment_links, money, new_purchase_number  # noqa: E402

PUBLIC_URL = os.environ.get("MERCHANT_PUBLIC_URL", "http://192.168.8.10:8002")
MAX_QTY = 24

catalog = Catalog.load()
payment_links = get_payment_links(PUBLIC_URL)
ORDERS: dict[str, dict] = {}
LINK_TO_ORDER: dict[str, str] = {}


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
    verification = verify_request(request.method, request.url.netloc, request.url.path, headers, body)
    if not verification.ok:
        await events.emit("signature_rejected", checks=verification.checks)
        raise HTTPException(401, {"error": "signature rejected", "checks": verification.checks})

    try:
        order_req = OrderRequest.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    ids = {"session_id": order_req.session_id, "mandate_id": order_req.mandate_id, "decision_id": order_req.decision_id}
    await events.emit("signature_verified", keyid=verification.keyid, checks=verification.checks, **ids)

    # Price from our own catalog; the agent only says which SKUs and how many.
    lines, total_cents = [], 0
    for line in order_req.cart.items:
        item = catalog.items.get(line.sku)
        if item is None:
            raise HTTPException(422, f"unknown sku {line.sku}")
        cents = round(item["price"] * 100)
        total_cents += cents * line.qty
        lines.append({"sku": line.sku, "name": item["name"], "qty": line.qty, "unit_price": money(cents / 100),
                      "category": item["category"]})
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
        "verification": verification.checks,
        "created_at": time.time(),
        "paid_at": None,
    }
    ORDERS[order_id] = order
    LINK_TO_ORDER[link.id] = order_id
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
        await events.emit("paid", order_id=order_id, amount=order["amount"], via=via, session_id=order["session_id"],
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


@app.get("/panel")
def panel():
    return {
        "merchant": "Corner Market",
        "backend": payment_links.backend,
        "visa_last_error": getattr(payment_links, "last_error", None),
        "orders": list_orders()[:10],
        "events": list(events.RECENT)[-50:],
    }


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
