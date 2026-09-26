"""Seed OTC medicines from openFDA drug labels into catalog/raw/openfda.json (no API key needed).

openFDA has no prices, so each item gets a stable made-up price. Then run build_catalog.

    python -m catalog.seed_openfda && python -m catalog.build_catalog
"""

import hashlib
import json
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "raw" / "openfda.json"
URL = "https://api.fda.gov/drug/label.json"
PER_INGREDIENT = 3

# generic name -> search group (see GROUP_ALIASES in build_catalog.py)
INGREDIENTS = {
    "ibuprofen": "pain_relief",
    "acetaminophen": "pain_relief",
    "naproxen sodium": "pain_relief",
    "aspirin": "pain_relief",
    "loratadine": "allergy",
    "cetirizine hydrochloride": "allergy",
    "famotidine": "heartburn",
    "calcium carbonate": "heartburn",
}


def stable_price(key: str, low: float = 4.99, high: float = 14.99) -> float:
    frac = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return round(round(low + frac * (high - low)) - 0.01, 2)


def fetch(client: httpx.Client, generic: str) -> list[dict]:
    search = f'openfda.product_type:"HUMAN OTC DRUG" AND openfda.generic_name:"{generic}"'
    r = client.get(URL, params={"search": search, "limit": 50})
    if r.status_code == 404:  # openFDA's "no matches"
        return []
    r.raise_for_status()
    return r.json().get("results", [])


def to_item(label: dict, generic: str, group: str) -> dict | None:
    fda = label.get("openfda", {})
    brand = (fda.get("brand_name") or [""])[0].strip()
    maker = (fda.get("manufacturer_name") or [""])[0].strip()
    set_id = label.get("set_id") or label.get("id")
    if not brand or not set_id:
        return None
    # Adult, single-ingredient products only (no children's formulas or combination/homeopathic labels).
    if [g.lower() for g in fda.get("generic_name", [])] != [generic]:
        return None
    if any(w in brand.lower() for w in ("child", "infant", "pediatric", "junior", "kids")):
        return None
    key = generic.split()[0]
    return {
        "sku": f"FDA-{set_id[:8].upper()}",
        "name": f"{brand.title()} ({generic})",
        "brand": maker.title() or brand.title(),
        "category": "otc_medicine",
        "group": group,
        "price": stable_price(set_id),
        "size": "",
        "tags": [key.replace(" ", "_")],
        "substitute_key": key,
        "merchant": "corner_market",
        "source": "openfda",
        "purpose": " ".join(label.get("purpose", []))[:200],
    }


def main():
    items, seen_brands = [], set()
    with httpx.Client(timeout=20) as client:
        for generic, group in INGREDIENTS.items():
            kept = 0
            for label in fetch(client, generic):
                it = to_item(label, generic, group)
                if it is None or it["name"].lower() in seen_brands:
                    continue
                seen_brands.add(it["name"].lower())
                items.append(it)
                kept += 1
                if kept == PER_INGREDIENT:
                    break
            print(f"{generic:26} {kept} items")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name}: {len(items)} items")


if __name__ == "__main__":
    main()
