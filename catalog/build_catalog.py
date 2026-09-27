"""Build catalog/catalog.json.

Demo-critical items are hand-set here so prices never drift between runs
(bread $3.49 + blood pressure pickup $8.00 = $11.49, Ensure case $52.00).
Anything the seeders cached in catalog/raw/*.json (Kroger, openFDA) is merged
on top; a missing or broken raw file is skipped, so this always produces a catalog.

Stores: every item's merchant comes from its category (STORE_BY_CATEGORY): pharmacy and
over-the-counter items are Parkside Pharmacy's, household items Main Street Home's, groceries Corner Market's.
Parkside also sells PARKSIDE_BASICS at its own prices, as PK-<sku> items; product_key ties the copies of one
product together so search can say where else it's sold.

    python -m catalog.build_catalog
"""

import json
import re
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "raw"
OUT = HERE / "catalog.json"
MERCHANT = "corner_market"
# Gift and prepaid cards are only sold by the blocked shop, so the merchant's 403 for it is real as well as R1.
STORE_BY_CATEGORY = {"otc_medicine": "parkside_pharmacy", "pharmacy_pickup": "parkside_pharmacy",
                     "personal_care": "parkside_pharmacy", "household": "main_street_home",
                     "gift_card": "quickgift_cards", "prepaid_card": "quickgift_cards"}
# Name-brand grocery basics a drugstore stocks. Its own prices: groceries cost more there, nutrition shakes less.
PARKSIDE_BASICS = ["BAK-001", "BAK-004", "BAK-005", "DAI-003", "DAI-004", "EGG-002", "PRO-001", "SOU-001",
                   "SOU-002", "SOU-003", "PAN-001", "PAN-002", "BEV-001", "BEV-002", "BEV-003", "NUT-001",
                   "NUT-003", "NUT-004"]
PARKSIDE_MARKUP = {"nutrition": 0.92}
PARKSIDE_DEFAULT_MARKUP = 1.10

# Search groups -> words shoppers use for them (en / es / hi incl. Latin-transliterated Hindi).
GROUP_ALIASES = {
    "bread": ["bread", "loaf", "pan", "pan de molde", "double roti", "bread ka packet", "ब्रेड"],
    "milk": ["milk", "leche", "doodh", "दूध"],
    "eggs": ["eggs", "egg", "huevos", "huevo", "ande", "anda", "अंडे", "अंडा"],
    "bananas": ["bananas", "banana", "platanos", "platano", "banano", "kela", "kele", "केला", "केले"],
    "soup": ["soup", "sopa", "chicken soup", "caldo", "सूप"],
    "oatmeal": ["oatmeal", "oats", "avena", "ओट्स"],
    "tea": ["tea", "te", "chai", "चाय"],
    "nutrition_shake": ["ensure", "shake", "nutrition shake", "protein shake", "batido", "malteada"],
    "pain_relief": ["pain", "pain reliever", "headache", "ibuprofen", "ibuprofeno", "advil", "motrin",
                    "acetaminophen", "acetaminofen", "paracetamol", "tylenol", "dard ki dawai", "दर्द की दवा"],
    "allergy": ["allergy", "allergies", "alergia", "zyrtec", "claritin", "loratadine", "cetirizine", "allergy ki dawai"],
    "heartburn": ["heartburn", "acid", "antacid", "acidez", "agruras", "pepcid", "tums", "acidity", "gas ki dawai"],
    # Groups below are filled by seed_kroger.py when Kroger credentials exist.
    "rice": ["rice", "arroz", "chawal", "चावल"],
    "beans": ["beans", "frijoles", "rajma", "canned beans"],
    "yogurt": ["yogurt", "yoghurt", "yogur", "dahi", "दही"],
    "cheese": ["cheese", "queso", "paneer"],
    "apples": ["apples", "apple", "manzanas", "manzana", "seb", "सेब"],
    "juice": ["juice", "orange juice", "jugo", "jugo de naranja", "juice ka dabba"],
    "coffee": ["coffee", "cafe", "coffee powder"],
    "crackers": ["crackers", "galletas saladas", "saltines"],
    "peanut_butter": ["peanut butter", "crema de cacahuate", "mantequilla de mani"],
    "cereal": ["cereal", "cornflakes", "hojuelas"],
    "prescription": ["prescription", "pharmacy", "pickup", "receta", "farmacia", "dawai", "दवाई"],
    "gift_card": ["gift card", "gift cards", "tarjeta de regalo", "tarjetas de regalo", "google play",
                  "itunes", "apple card", "steam card", "गिफ्ट कार्ड"],
    "prepaid_card": ["prepaid card", "reloadable card", "tarjeta prepagada", "prepaid"],
    # Main Street Home (seed_kroger.py --household)
    "paper_towels": ["paper towels", "paper towel", "toallas de papel", "papel de cocina", "kitchen towel"],
    "toilet_paper": ["toilet paper", "bath tissue", "papel higienico", "papel de bano", "toilet roll"],
    "batteries": ["batteries", "battery", "pilas", "baterias", "बैटरी"],
    "light_bulbs": ["light bulb", "light bulbs", "bulb", "bulbs", "bombilla", "bombillo", "foco", "focos",
                    "बल्ब"],
    "dish_soap": ["dish soap", "dishwashing liquid", "jabon de platos", "lavaplatos", "bartan sabun"],
    "laundry_detergent": ["laundry detergent", "detergent", "detergente", "jabon de ropa", "kapde dhone ka sabun",
                          "washing powder"],
    "trash_bags": ["trash bags", "garbage bags", "bolsas de basura", "kachre ki thaili", "bin bags"],
}


def item(sku, name, brand, category, group, price, size, tags=(), **extra):
    return {
        "sku": sku,
        "name": name,
        "brand": brand,
        "category": category,
        "group": group,
        "price": price,
        "size": size,
        "tags": list(tags),
        "merchant": MERCHANT,
        "source": "synthetic",
        **extra,
    }


SYNTHETIC = [
    # bread (BAK-001 is Ruth's usual, see profile.json)
    item("BAK-001", "Nature's Own Honey Wheat Bread", "Nature's Own", "bakery", "bread", 3.49, "20 oz", ["soft", "wheat"]),
    item("BAK-002", "Kroger Whole Wheat Bread", "Kroger", "bakery", "bread", 2.99, "20 oz", ["soft", "whole_grain", "store_brand"]),
    item("BAK-003", "Kroger Low Sodium Whole Wheat Bread", "Kroger", "bakery", "bread", 3.19, "16 oz", ["low_sodium", "whole_grain", "store_brand"]),
    item("BAK-004", "Dave's Killer Bread 21 Whole Grains", "Dave's Killer Bread", "bakery", "bread", 5.99, "27 oz", ["whole_grain", "high_fiber"]),
    item("BAK-005", "Nature's Own Butterbread", "Nature's Own", "bakery", "bread", 3.29, "20 oz", ["soft", "white"]),
    # milk
    item("DAI-001", "Kroger 2% Reduced Fat Milk", "Kroger", "dairy", "milk", 3.29, "1 gal", ["store_brand"]),
    item("DAI-002", "Kroger 2% Reduced Fat Milk", "Kroger", "dairy", "milk", 2.19, "half gal", ["store_brand", "small_pack"]),
    item("DAI-003", "Lactaid 2% Reduced Fat Milk", "Lactaid", "dairy", "milk", 4.99, "96 oz", ["lactose_free"]),
    item("DAI-004", "Horizon Organic Whole Milk", "Horizon", "dairy", "milk", 5.49, "half gal", ["organic"]),
    # eggs
    item("EGG-001", "Kroger Grade A Large Eggs", "Kroger", "dairy", "eggs", 2.79, "12 ct", ["store_brand"]),
    item("EGG-002", "Eggland's Best Large Eggs", "Eggland's Best", "dairy", "eggs", 4.49, "12 ct", []),
    item("EGG-003", "Kroger Grade A Large Eggs", "Kroger", "dairy", "eggs", 1.69, "6 ct", ["store_brand", "small_pack"]),
    # bananas
    item("PRO-001", "Bananas", "Fresh", "produce", "bananas", 0.62, "1 lb", ["soft"]),
    item("PRO-002", "Organic Bananas", "Simple Truth", "produce", "bananas", 1.99, "2 lb bunch", ["soft", "organic"]),
    # soup
    item("SOU-001", "Campbell's Chicken Noodle Soup", "Campbell's", "pantry", "soup", 1.49, "10.75 oz", ["easy_open"]),
    item("SOU-002", "Campbell's Healthy Request Chicken Noodle Soup", "Campbell's", "pantry", "soup", 1.99, "10.75 oz", ["low_sodium", "heart_healthy", "easy_open"]),
    item("SOU-003", "Progresso Reduced Sodium Chicken Noodle Soup", "Progresso", "pantry", "soup", 2.99, "19 oz", ["low_sodium", "ready_to_eat"]),
    item("SOU-004", "Kroger Chicken Noodle Soup", "Kroger", "pantry", "soup", 0.99, "10.75 oz", ["store_brand"]),
    # oatmeal
    item("PAN-001", "Quaker Old Fashioned Oats", "Quaker", "pantry", "oatmeal", 4.29, "18 oz", ["whole_grain", "heart_healthy"]),
    item("PAN-002", "Quaker Instant Oatmeal Original", "Quaker", "pantry", "oatmeal", 4.49, "10 ct", ["whole_grain", "easy_prep"]),
    item("PAN-003", "Kroger Old Fashioned Oats", "Kroger", "pantry", "oatmeal", 2.79, "18 oz", ["whole_grain", "heart_healthy", "store_brand"]),
    # tea
    item("BEV-001", "Lipton Black Tea Bags", "Lipton", "beverages", "tea", 5.49, "100 ct", []),
    item("BEV-002", "Celestial Seasonings Chamomile Herbal Tea", "Celestial Seasonings", "beverages", "tea", 3.49, "20 ct", ["caffeine_free"]),
    item("BEV-003", "Wagh Bakri Masala Chai", "Wagh Bakri", "beverages", "tea", 6.99, "250 g", []),
    # nutrition shakes (NUT-002 is the $52 approval case)
    item("NUT-001", "Ensure Original Vanilla Nutrition Shake", "Ensure", "nutrition", "nutrition_shake", 10.99, "6 x 8 fl oz", ["protein"]),
    item("NUT-002", "Ensure Original Vanilla Nutrition Shake, Case", "Ensure", "nutrition", "nutrition_shake", 52.00, "24 x 8 fl oz", ["protein", "bulk"]),
    item("NUT-003", "Ensure Max Protein Milk Chocolate Shake", "Ensure", "nutrition", "nutrition_shake", 9.99, "4 x 11 fl oz", ["protein", "high_protein"]),
    item("NUT-004", "Glucerna Original Shake Homemade Vanilla", "Glucerna", "nutrition", "nutrition_shake", 11.29, "6 x 8 fl oz", ["protein", "diabetes_friendly"]),
    # OTC pain relief
    item("OTC-001", "Advil Ibuprofen 200 mg Tablets", "Advil", "otc_medicine", "pain_relief", 12.99, "100 ct", ["ibuprofen", "nsaid"], substitute_key="ibuprofen"),
    item("OTC-002", "Kroger Ibuprofen 200 mg Tablets", "Kroger", "otc_medicine", "pain_relief", 6.49, "100 ct", ["ibuprofen", "nsaid", "store_brand"], substitute_key="ibuprofen"),
    item("OTC-003", "Tylenol Extra Strength 500 mg Caplets", "Tylenol", "otc_medicine", "pain_relief", 11.99, "100 ct", ["acetaminophen"], substitute_key="acetaminophen"),
    item("OTC-004", "Kroger Extra Strength Acetaminophen 500 mg", "Kroger", "otc_medicine", "pain_relief", 5.99, "100 ct", ["acetaminophen", "store_brand"], substitute_key="acetaminophen"),
    # pharmacy pickup (Ruth's "blood pressure medicine", see profile.json)
    item("RX-001", "Lisinopril 10 mg, 30 tablets (pharmacy pickup)", "Parkside Pharmacy", "pharmacy_pickup", "prescription", 8.00, "30 tablets",
         ["prescription", "pickup", "blood_pressure"], note="Ready for pickup, $8.00 copay"),
    # blocked by the default mandate (R1). Stocked on purpose so the refusal is a policy decision, not a missing item.
    item("GFT-001", "Google Play Gift Card", "Google Play", "gift_card", "gift_card", 100.00, "$100", ["gift_card"]),
    item("GFT-002", "Apple Gift Card", "Apple", "gift_card", "gift_card", 200.00, "$200", ["gift_card"]),
    item("GFT-003", "Target Gift Card", "Target", "gift_card", "gift_card", 50.00, "$50", ["gift_card"]),
    item("PPD-001", "Reloadable Prepaid Debit Card", "PrePaid Plus", "prepaid_card", "prepaid_card", 200.00, "$200 load", ["prepaid"]),
    # Main Street Home basics, so household works without Kroger credentials too
    item("HOM-001", "Bounty Select-A-Size Paper Towels, 6 Double Rolls", "Bounty", "household", "paper_towels", 15.29, "6 rolls"),
    item("HOM-002", "Charmin Ultra Soft Toilet Paper, 6 Mega Rolls", "Charmin", "household", "toilet_paper", 9.29, "6 rolls", ["soft"]),
    item("HOM-003", "Duracell Coppertop AA Batteries, 8 Pack", "Duracell", "household", "batteries", 11.99, "8 ct"),
    item("HOM-004", "Philips 60-Watt A19 LED Light Bulb, Soft White", "Philips", "household", "light_bulbs", 9.99, "1 ct"),
    item("HOM-005", "Dawn Ultra Original Dish Soap", "Dawn", "household", "dish_soap", 3.49, "19.4 fl oz"),
    item("HOM-006", "Tide Original Liquid Laundry Detergent", "Tide", "household", "laundry_detergent", 15.99, "92 fl oz"),
    item("HOM-007", "Glad Tall Kitchen Drawstring Trash Bags, 13 Gallon", "Glad", "household", "trash_bags", 11.99, "40 ct"),
]


def load_raw():
    items = []
    for path in sorted(RAW.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"skip {path.name}: {e}")
            continue
        print(f"merge {path.name}: {len(data)} items")
        items.extend(data)
    return items


def same_product(a: str, b: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower())
    return norm(a).startswith(norm(b)) or norm(b).startswith(norm(a))


# Store category -> the category the caregiver's mandate talks about. Policy re-derives it
# from here and ignores whatever the station sends.
MANDATE_CATEGORY = {
    "bakery": "grocery", "beverages": "grocery", "dairy": "grocery", "nutrition": "grocery",
    "pantry": "grocery", "produce": "grocery", "frozen": "grocery", "meat": "grocery", "deli": "grocery",
    "pet": "grocery",
    "otc_medicine": "pharmacy", "pharmacy_pickup": "pharmacy", "personal_care": "pharmacy",
    "gift_card": "gift_card", "prepaid_card": "prepaid_card",
    "household": "household",
    # Alcohol and tobacco (live Kroger items): a category Priyank's rules don't list, so they are refused.
    "age_restricted": "age_restricted",
}


def parkside_price(it: dict) -> float:
    """Deterministic: the same catalog build always gives the same Parkside prices."""
    raw = it["price"] * PARKSIDE_MARKUP.get(it["category"], PARKSIDE_DEFAULT_MARKUP)
    return round(int(raw) + (0.49 if raw % 1 < 0.49 else 0.99), 2)


def parkside_copy(it: dict) -> dict:
    copy = {k: v for k, v in it.items() if k != "regular_price"}  # a promo at one store isn't one at the other
    return {**copy, "sku": "PK-" + it["sku"], "price": parkside_price(it), "merchant": "parkside_pharmacy",
            "product_key": it["sku"]}


def build():
    by_sku = {}
    for it in load_raw():
        # A real listing of a demo product would sit beside it at another price; the demo item wins.
        if not any(same_product(it["name"], demo["name"]) for demo in SYNTHETIC):
            by_sku[it["sku"]] = it
    for it in SYNTHETIC:
        by_sku[it["sku"]] = it
    for it in by_sku.values():
        it["merchant"] = STORE_BY_CATEGORY.get(it["category"], MERCHANT)
        it.setdefault("product_key", it["sku"])
    for sku in PARKSIDE_BASICS:
        copy = parkside_copy(by_sku[sku])
        by_sku[copy["sku"]] = copy
    for it in by_sku.values():
        if it["category"] not in MANDATE_CATEGORY:
            raise SystemExit(f"{it['sku']}: category {it['category']!r} has no mandate_category mapping")
        it["mandate_category"] = MANDATE_CATEGORY[it["category"]]
    catalog = {
        "merchant": MERCHANT,  # the default store; each item names its own
        "currency": "USD",
        "group_aliases": GROUP_ALIASES,
        "items": sorted(by_sku.values(), key=lambda it: (it["group"], it["price"])),
    }
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    per_store = {}
    for it in catalog["items"]:
        per_store[it["merchant"]] = per_store.get(it["merchant"], 0) + 1
    print(f"wrote {OUT.relative_to(HERE.parent)}: {len(catalog['items'])} items, {len(GROUP_ALIASES)} groups, {per_store}")
    return catalog


if __name__ == "__main__":
    build()
