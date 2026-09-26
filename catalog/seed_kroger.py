"""Seed groceries from the Kroger public API (one Atlanta store) into catalog/raw/kroger.json.

Prices are the store's own: price.promo when a promotion is on (with price.regular kept as regular_price for
the receipt's savings line), else price.regular. No promo today means no savings; none are invented.

Needs KROGER_CLIENT_ID / KROGER_CLIENT_SECRET (developer.kroger.com, scope product.compact).
Without them this exits and build_catalog uses the synthetic catalog only.

    python -m catalog.seed_kroger && python -m catalog.build_catalog
    python -m catalog.seed_kroger --household   # only HOUSEHOLD_TERMS -> raw/kroger_household.json (Main Street
                                                # Home); the grocery file and its prices stay as they are
"""

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Production app -> api.kroger.com; a Certification (test) app -> https://api-ce.kroger.com/v1
API = os.environ.get("KROGER_API", "https://api.kroger.com/v1")
OUT = Path(__file__).parent / "raw" / "kroger.json"
OUT_HOUSEHOLD = Path(__file__).parent / "raw" / "kroger_household.json"
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

# Main Street Home's shelf; build_catalog assigns category household to main_street_home.
HOUSEHOLD_TERMS = {
    "paper towels": ("paper_towels", "household"),
    "toilet paper": ("toilet_paper", "household"),
    "batteries": ("batteries", "household"),
    "light bulbs": ("light_bulbs", "household"),
    "dish soap": ("dish_soap", "household"),
    "laundry detergent": ("laundry_detergent", "household"),
    "trash bags": ("trash_bags", "household"),
}

# Kroger search is fuzzy ("bananas" returns banana peppers and smoothies); keep only real matches.
# A tuple means any one of the words.
MUST_CONTAIN = {"bananas": "banana", "apples": "apple", "eggs": "egg", "milk": "milk",
                "paper towels": "towel", "toilet paper": ("bath tissue", "toilet"), "batteries": "batter",
                "light bulbs": "bulb", "dish soap": "dish", "laundry detergent": "detergent",
                "trash bags": ("trash", "garbage", "bag")}
EXCLUDE_WORDS = {
    "bananas": ["pepper", "smoothie", "chip", "trail", "dried", "protein", "yogurt", "pouch", "boat", "sunscreen",
                "juice", "nectar", "drink", "bread", "muffin", "pudding", "almond", "snaps", "crispy", "kids"],
    "apples": ["caramel", "juice", "sauce", "chip", "cider", "vinegar", "pie", "drink", "snack"],
    "paper towels": ["holder", "dispenser"],
    "toilet paper": ["holder", "cleaner", "wipes"],
    "batteries": ["charger", "tester", "cake mix", "batter mix", "pancake"],
    "light bulbs": ["fixture"],
    "dish soap": ["dispenser", "brush", "rack"],
    "laundry detergent": ["dispenser"],
}

TAG_WORDS = {
    "low sodium": "low_sodium", "reduced sodium": "low_sodium", "less sodium": "low_sodium",
    "no salt": "low_sodium", "unsalted": "low_sodium", "lower sugar": "reduced_sugar", "less sugar": "reduced_sugar", "organic": "organic", "lactose free": "lactose_free",
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


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[®™]", "", text)).strip()


def wanted(term: str, name: str) -> bool:
    lower = name.lower()
    must = MUST_CONTAIN.get(term)
    if must and not any(w in lower for w in ((must,) if isinstance(must, str) else must)):
        return False
    return not any(w in lower for w in EXCLUDE_WORDS.get(term, []))


def to_item(p: dict, group: str, category: str) -> dict | None:
    offer = (p.get("items") or [{}])[0]
    price = offer.get("price") or {}
    regular, promo = float(price.get("regular") or 0), float(price.get("promo") or 0)  # promo is 0 when none
    amount = promo if 0 < promo < regular else regular
    if not amount:
        return None  # not sold at this store
    name = clean(p.get("description", ""))
    brand = clean(p.get("brand") or "")
    text = f"{name} {brand}".lower()
    return {
        "sku": f"KR-{p['productId']}",
        "name": name,
        "brand": brand,
        "category": category,
        "group": group,
        "price": round(amount, 2),  # what we charge: the store's promo price when it has one
        # the shelf price, only when a promo is on today; the receipt's "You saved" comes from this
        **({"regular_price": round(regular, 2)} if amount < regular else {}),
        "size": offer.get("size", ""),
        "tags": sorted({tag for words, tag in TAG_WORDS.items() if words in text}),
        "merchant": "corner_market",
        "source": "kroger",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--household", action="store_true", help="seed only HOUSEHOLD_TERMS into kroger_household.json")
    args = ap.parse_args()
    terms, out = (HOUSEHOLD_TERMS, OUT_HOUSEHOLD) if args.household else (TERMS, OUT)
    client_id, secret = os.environ.get("KROGER_CLIENT_ID"), os.environ.get("KROGER_CLIENT_SECRET")
    if not client_id or not secret:
        sys.exit("KROGER_CLIENT_ID / KROGER_CLIENT_SECRET not set; synthetic catalog only")
    headers = {"Authorization": f"Bearer {token(client_id, secret)}", "Accept": "application/json"}
    items = {}
    with httpx.Client(headers=headers, timeout=15) as client:
        loc = nearest_location(client, os.environ.get("KROGER_ZIP", "30308"))
        print(f"store: {loc['name']} ({loc['locationId']}) {loc['address']['addressLine1']}")
        for term, (group, category) in terms.items():
            r = client.get(f"{API}/products", params={"filter.term": term, "filter.locationId": loc["locationId"],
                                                      "filter.limit": 50})
            r.raise_for_status()
            kept = 0
            for p in r.json().get("data", []):
                it = to_item(p, group, category)
                if it and it["sku"] not in items and wanted(term, it["name"]):
                    items[it["sku"]] = it
                    kept += 1
                    if kept == PER_TERM:
                        break
            print(f"{term:24} {kept} items")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(list(items.values()), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.name}: {len(items)} items (location {loc['locationId']})")


if __name__ == "__main__":
    main()
