"""Re-price a checkout cart from the local catalog. The station's prices are ignored."""

from __future__ import annotations

from catalog.search import Catalog
from policy.engine import mandate_category, to_cents, dollars

_catalog: Catalog | None = None


def catalog() -> Catalog:
    global _catalog
    if _catalog is None:
        _catalog = Catalog.load()
    return _catalog


class UnknownSku(Exception):
    def __init__(self, sku: str):
        super().__init__(sku)
        self.sku = sku


def reprice(cart: dict) -> dict:
    items = []
    for line in cart.get("items") or []:
        sku = line["sku"]
        item = catalog().item(sku)
        if item is None:
            raise UnknownSku(sku)
        qty = int(line.get("qty") or 1)
        priced = {
            "sku": sku,
            "name": item["name"],
            "category": item["category"],
            "mandate_category": mandate_category(item),
            "qty": qty,
            "price": item["price"],
            "merchant": item.get("merchant") or cart.get("merchant") or "corner_market",
        }
        items.append(priced)
    total_cents = sum(to_cents(item["price"]) * item["qty"] for item in items)
    if cart.get("total") is not None and to_cents(cart["total"]) != total_cents:
        print(f"station total {cart['total']} replaced with {dollars(total_cents):.2f}")
    return {"merchant": cart.get("merchant") or "corner_market", "items": items, "total": dollars(total_cents)}
