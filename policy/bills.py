"""Price a power-bill line from the biller, not from the grocery catalog."""

from __future__ import annotations

import httpx

from common.config import merchant_public_url
from policy.engine import dollars, to_cents
from policy.pricing import UnknownSku, reprice

BILL_SKU = "BILL-peachtree_power"


class BillError(Exception):
    pass


def _account(ref: str, fetch) -> dict:
    if fetch is not None:
        return fetch(ref)
    url = f"{merchant_public_url()}/billers/peachtree_power/accounts/{ref}?purpose=price"
    try:
        response = httpx.get(url, timeout=2)
    except httpx.HTTPError as exc:
        raise BillError("the power company did not answer") from exc
    if response.status_code >= 400:
        raise BillError("the power company did not answer")
    return response.json()


def price_bill(line: dict, mandate: dict, fetch=None) -> dict:
    sku = str(line.get("sku") or "")
    if sku != BILL_SKU:
        raise UnknownSku(sku)
    biller = next((row for row in (mandate.get("billers") or []) if row.get("merchant_id") == "peachtree_power"), None)
    if not biller:
        raise BillError("no power account on the mandate")
    account = _account(str(biller.get("account_ref") or ""), fetch)
    amount = float(account.get("balance_due") or 0)
    cap = float(biller.get("monthly_cap") or 0)
    if cap and amount > cap + 0.001:
        raise BillError("power bill is over the monthly cap")
    return {
        "sku": sku,
        "name": f"Peachtree Power bill {biller.get('account_ref')}",
        "category": "utility_bill",
        "mandate_category": "utility_bill",
        "qty": 1,
        "price": amount,
        "merchant": "peachtree_power",
    }


def price_cart(cart: dict, mandate: dict, fetch=None) -> dict:
    items = list(cart.get("items") or [])
    bills = [line for line in items if str(line.get("sku") or "").startswith("BILL-")]
    goods = [line for line in items if not str(line.get("sku") or "").startswith("BILL-")]
    if goods:
        priced = reprice({**cart, "items": goods})
    else:
        priced = {"merchant": cart.get("merchant") or "peachtree_power", "items": [], "total": 0}
    for line in bills:
        priced["items"].append(price_bill(line, mandate, fetch))
        priced["merchant"] = "peachtree_power" if not goods else priced["merchant"]
    total_cents = sum(to_cents(item["price"]) * int(item["qty"]) for item in priced["items"])
    priced["total"] = dollars(total_cents)
    return priced
