"""Build catalog/catalog.json.

Demo-critical items are hand-set here so prices never drift between runs
(bread $3.49 + blood pressure pickup $8.00 = $11.49, Ensure case $52.00).
Anything the seeders cached in catalog/raw/*.json (Kroger, openFDA) is merged
on top; a missing or broken raw file is skipped, so this always produces a catalog.

    python -m catalog.build_catalog
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "raw"
OUT = HERE / "catalog.json"
MERCHANT = "corner_market"

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
    item("RX-001", "Lisinopril 10 mg, 30 tablets (pharmacy pickup)", "Corner Market Pharmacy", "pharmacy_pickup", "prescription", 8.00, "30 tablets",
         ["prescription", "pickup", "blood_pressure"], note="Ready for pickup, $8.00 copay"),
    # blocked by the default mandate (R1). Stocked on purpose so the refusal is a policy decision, not a missing item.
    item("GFT-001", "Google Play Gift Card", "Google Play", "gift_card", "gift_card", 100.00, "$100", ["gift_card"]),
    item("GFT-002", "Apple Gift Card", "Apple", "gift_card", "gift_card", 200.00, "$200", ["gift_card"]),
    item("GFT-003", "Target Gift Card", "Target", "gift_card", "gift_card", 50.00, "$50", ["gift_card"]),
    item("PPD-001", "Reloadable Prepaid Debit Card", "PrePaid Plus", "prepaid_card", "prepaid_card", 200.00, "$200 load", ["prepaid"]),
]


def load_raw():
    items = []
    for path in sorted(RAW.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"skip {path.name}: {e}")
            continue
        print(f"merge {path.name}: {len(data)} items")
        items.extend(data)
    return items


def build():
    by_sku = {}
    for it in load_raw() + SYNTHETIC:  # synthetic last so demo items always win
        by_sku[it["sku"]] = it
    catalog = {
        "merchant": MERCHANT,
        "currency": "USD",
        "group_aliases": GROUP_ALIASES,
        "items": sorted(by_sku.values(), key=lambda it: (it["group"], it["price"])),
    }
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.relative_to(HERE.parent)}: {len(catalog['items'])} items, {len(GROUP_ALIASES)} groups")
    return catalog


if __name__ == "__main__":
    build()
