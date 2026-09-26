"""Seed groceries from the Kroger public API (one Atlanta store) into catalog/raw/kroger.json.

Needs KROGER_CLIENT_ID / KROGER_CLIENT_SECRET (developer.kroger.com, scope product.compact).
Without them this exits and build_catalog uses the synthetic catalog only.

    python -m catalog.seed_kroger && python -m catalog.build_catalog
"""

import base64
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Production app -> api.kroger.com; a Certification (test) app -> https://api-ce.kroger.com/v1
API = os.environ.get("KROGER_API", "https://api.kroger.com/v1")
OUT = Path(__file__).parent / "raw" / "kroger.json"
PER_TERM = 15

# search term -> (group, category); groups must exist in build_catalog.GROUP_ALIASES
TERMS = {
    "bread": ("bread", "bakery"),
    "milk": ("milk", "dairy"),
    "eggs": ("eggs", "dairy"),
    "bananas": ("bananas", "produce"),
    "apples": ("apples", "produce"),
    "chicken noodle soup": ("soup", "pantry"),
    "oatmeal": ("oatmeal", "pantry"),
    "tea bags": ("tea", "beverages"),
    "ensure nutrition shake": ("nutrition_shake", "nutrition"),
    "rice": ("rice", "pantry"),
    "canned beans": ("beans", "pantry"),
    "yogurt": ("yogurt", "dairy"),
    "cheese": ("cheese", "dairy"),
    "orange juice": ("juice", "beverages"),
    "coffee": ("coffee", "beverages"),
    "saltine crackers": ("crackers", "pantry"),
    "peanut butter": ("peanut_butter", "pantry"),
    "cereal": ("cereal", "pantry"),
    "low sodium soup": ("soup", "pantry"),
}

TAG_WORDS = {
    "low sodium": "low_sodium", "reduced sodium": "low_sodium", "organic": "organic", "lactose free": "lactose_free",
    "whole grain": "whole_grain", "whole wheat": "whole_grain", "sugar free": "sugar_free", "decaf": "caffeine_free",
    "caffeine free": "caffeine_free", "kroger": "store_brand", "simple truth": "store_brand", "private selection": "store_brand",
}


def token(client_id: str, secret: str) -> str:
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    r = httpx.post(f"{API}/connect/oauth2/token", headers={"Authorization": f"Basic {basic}"},
                   data={"grant_type": "client_credentials", "scope": "product.compact"}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def nearest_location(client: httpx.Client, zip_code: str) -> dict:
    r = client.get(f"{API}/locations", params={"filter.zipCode.near": zip_code, "filter.limit": 5})
    r.raise_for_status()
    locations = r.json()["data"]
    if not locations:
        raise SystemExit(f"no Kroger locations near {zip_code}")
    return locations[0]


def to_item(p: dict, group: str, category: str) -> dict | None:
    offer = (p.get("items") or [{}])[0]
    price = offer.get("price") or {}
    amount = price.get("promo") or price.get("regular")
    if not amount:
        return None  # not sold at this store
    name = p.get("description", "").strip()
    brand = (p.get("brand") or "").strip()
    text = f"{name} {brand}".lower()
    return {
        "sku": f"KR-{p['productId']}",
        "name": name,
        "brand": brand,
        "category": category,
        "group": group,
        "price": round(float(amount), 2),
        "size": offer.get("size", ""),
        "tags": sorted({tag for words, tag in TAG_WORDS.items() if words in text}),
        "merchant": "corner_market",
        "source": "kroger",
    }


def main():
    client_id, secret = os.environ.get("KROGER_CLIENT_ID"), os.environ.get("KROGER_CLIENT_SECRET")
    if not client_id or not secret:
        sys.exit("KROGER_CLIENT_ID / KROGER_CLIENT_SECRET not set; synthetic catalog only")
    headers = {"Authorization": f"Bearer {token(client_id, secret)}", "Accept": "application/json"}
    items = {}
    with httpx.Client(headers=headers, timeout=15) as client:
        loc = nearest_location(client, os.environ.get("KROGER_ZIP", "30308"))
        print(f"store: {loc['name']} ({loc['locationId']}) {loc['address']['addressLine1']}")
        for term, (group, category) in TERMS.items():
            r = client.get(f"{API}/products", params={"filter.term": term, "filter.locationId": loc["locationId"],
                                                      "filter.limit": 50})
            r.raise_for_status()
            kept = 0
            for p in r.json().get("data", []):
                it = to_item(p, group, category)
                if it and it["sku"] not in items:
                    items[it["sku"]] = it
                    kept += 1
                    if kept == PER_TERM:
                        break
            print(f"{term:24} {kept} items")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(list(items.values()), indent=2) + "\n")
    print(f"wrote {OUT.name}: {len(items)} items (location {loc['locationId']})")


if __name__ == "__main__":
    main()
