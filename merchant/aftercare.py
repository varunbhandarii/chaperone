"""After payment: the order lifecycle, refunds in Cybersource's response shape, savings and loyalty points.

Lifecycle:
    awaiting_payment -> paid -> preparing -> ready_for_pickup -> picked_up
    side exits: cancelled (only from awaiting_payment); partially_refunded, refunded (from paid or later)
After paid, the merchant advances on timers: preparing after ORDER_PREPARING_S (20), ready_for_pickup
ORDER_READY_S (60) after paid. A refund changes `status` but pickup still goes on, so the fulfilment step is
kept separately in `fulfilment`; `timeline` records every change.

Refunds are a stub in the shape of Cybersource's POST /pts/v2/payments/{id}/refunds answer: sandbox
authorizations fail with reason 150, so nothing was ever captured that a real refund could return. Every
refund goes to the card that paid; there is no destination field anywhere.
"""

from __future__ import annotations

import datetime
import math
import os
import secrets
import time

FULFILMENT = ("paid", "preparing", "ready_for_pickup", "picked_up")
REFUND_STATES = ("partially_refunded", "refunded")
REFUNDABLE = FULFILMENT + ("partially_refunded",)
NOT_RETURNABLE = {"pharmacy_pickup": "refund_not_allowed_rx", "utility_bill": "refund_not_allowed_bill"}
RETURN_WINDOW_S = 30 * 24 * 3600
STUB_LABEL = "Refund to the original card · sandbox processor stub"
LOYALTY_PROGRAM = "Corner Market Rewards"
SANDBOX_CARD_LAST4 = "1111"  # the Visa sandbox test card; no real card is ever on file


def _seconds(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def preparing_after() -> float:
    return _seconds("ORDER_PREPARING_S", 20)


def ready_after() -> float:
    return _seconds("ORDER_READY_S", 60)


def transmit_after() -> float:
    return _seconds("REFUND_TRANSMIT_S", 5)


def iso(ts: float | None) -> str | None:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat() if ts else None


def cents(value) -> int:
    return round(float(value) * 100)


def dollars(value_cents: int) -> str:
    return f"{value_cents / 100:.2f}"


def new_pickup_code() -> str:
    return f"{secrets.randbelow(900) + 100}"


def record(order: dict, status: str, **detail) -> None:
    """Append a step to the timeline; `status` is the order status above, `fulfilment` keeps the pickup progress."""
    order.setdefault("timeline", []).append({"status": status, "at": iso(time.time()), **detail})
    if status in FULFILMENT:
        order["fulfilment"] = status
        if order["status"] not in REFUND_STATES:
            order["status"] = status
    else:
        order["status"] = status


def savings_cents(lines: list[dict]) -> int:
    return sum(max(0, cents(l["regular_price"]) - cents(l["unit_price"])) * l["qty"]
               for l in lines if l.get("regular_price"))


def loyalty_points(amount: str) -> int:
    """One point per whole dollar paid."""
    return math.floor(float(amount))


def refunded_cents(order: dict) -> int:
    return sum(cents(r["amount"]) for r in order.get("refunds", []))


def refunded_qty(order: dict, sku: str) -> int:
    return sum(r["qty"] for r in order.get("refunds", []) if r["sku"] == sku)


class RefundRefused(Exception):
    def __init__(self, http_status: int, error: str, **detail):
        super().__init__(error)
        self.http_status, self.error, self.detail = http_status, error, detail


def check_refund(order: dict, sku: str, qty: int, claimed_amount: str | None) -> tuple[dict, int]:
    """The merchant's own refund checks; policy runs RF1-RF6 before it signs. Returns (line, amount in cents)."""
    if order["status"] not in REFUNDABLE:
        raise RefundRefused(409, "order is not refundable", status=order["status"])
    if order.get("paid_at") and time.time() - order["paid_at"] > RETURN_WINDOW_S:
        raise RefundRefused(409, "outside the 30-day return window")
    line = next((l for l in order["lines"] if l["sku"] == sku), None)
    if line is None:
        raise RefundRefused(422, f"sku {sku} is not in order {order['order_id']}")
    if line.get("category") in NOT_RETURNABLE:
        raise RefundRefused(409, "not returnable", say_key=NOT_RETURNABLE[line["category"]], sku=sku)
    left_qty = line["qty"] - refunded_qty(order, sku)
    if qty > left_qty:
        raise RefundRefused(409, "more than was bought", sku=sku, refundable_qty=left_qty)
    amount = cents(line["unit_price"]) * qty
    remaining = cents(order["amount"]) - refunded_cents(order)
    if amount > remaining:
        raise RefundRefused(409, "more than the amount left to refund", remaining=dollars(remaining))
    if claimed_amount is not None and cents(claimed_amount) != amount:
        raise RefundRefused(422, "amount does not match the order's own price", amount=dollars(amount))
    return line, amount


def refund_response(order: dict, amount_cents: int) -> dict:
    """Cybersource's refund answer (PtsV2PaymentsRefundPost201Response), from the stub processor."""
    return {
        "id": "".join(str(secrets.randbelow(10)) for _ in range(22)),
        "status": "PENDING",
        "submitTimeUtc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reconciliationId": secrets.token_hex(8).upper(),
        "clientReferenceInformation": {"code": order["order_id"]},
        "refundAmountDetails": {"refundAmount": dollars(amount_cents), "currency": order["currency"]},
        "processorInformation": {"responseCode": "100", "approvalCode": f"{secrets.randbelow(10**6):06d}"},
        "source": "sandbox-processor-stub",
        "label": STUB_LABEL,
    }
