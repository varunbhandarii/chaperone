import time

import pytest

from catalog.search import Catalog

catalog = Catalog.load()


def skus(items):
    return [it["sku"] for it in items]


@pytest.mark.parametrize("q, group", [
    ("bread", "bread"), ("pan", "bread"), ("leche", "milk"), ("doodh", "milk"), ("दूध", "milk"),
    ("huevos", "eggs"), ("kela", "bananas"), ("sopa", "soup"), ("avena", "oatmeal"), ("chai", "tea"),
    ("ensure", "nutrition_shake"), ("ibuprofeno", "pain_relief"), ("gift cards", "gift_card"),
    ("tarjetas de regalo", "gift_card"),
])
def test_ten_terms_in_three_languages(q, group):
    results = catalog.search(q, 3)
    assert results and results[0]["group"] == group


def test_brand_name_beats_group_alias():
    assert catalog.search("tylenol", 1)[0]["sku"] == "OTC-003"
    assert "chai" in catalog.search("chai", 1)[0]["name"].lower()


def test_usual_bread_ranks_first():
    top = catalog.search("bread", 1)[0]
    assert top["sku"] == "BAK-001" and top["usual"]


def test_apples_never_surface_gift_cards():
    assert not any(it["category"] == "gift_card" for it in catalog.search("apples"))


@pytest.mark.parametrize("q", [
    "my blood pressure medicine and bread",
    "mi medicina de la presión y pan",
    "meri bp ki dawai aur bread",
])
def test_resolve_demo_line_totals_1149(q):
    matches = catalog.resolve(q)
    assert sorted(m["sku"] for m in matches) == ["BAK-001", "RX-001"]
    assert round(sum(m["item"]["price"] for m in matches), 2) == 11.49


def test_resolve_word_boundaries():
    assert catalog.resolve("pantry staples") == []


def test_suggest_low_sodium_bread_for_ruth():
    alts = catalog.suggest("BAK-001")
    assert alts[0]["sku"] == "BAK-003" and "low sodium" in alts[0]["reasons"]


def test_suggest_keeps_active_ingredient():
    for alt in catalog.suggest("OTC-001", limit=10):
        assert alt["substitute_key"] == "ibuprofen"


def test_no_suggestions_for_gift_cards_or_prescriptions():
    assert catalog.suggest("GFT-001") == []
    assert catalog.suggest("RX-001") == []


def test_suggest_respects_budget():
    assert all(a["price"] <= 3.0 for a in catalog.suggest("BAK-001", budget=3.0))


def test_search_under_100ms():
    start = time.perf_counter()
    for _ in range(20):
        catalog.search("low sodium chicken noodle soup")
    assert (time.perf_counter() - start) / 20 < 0.1


def test_spanish_pan_is_bread_not_peter_pan():
    assert catalog.search("pan", 1)[0]["group"] == "bread"


def test_spanish_tag_words_find_low_sodium():
    top = catalog.search("sopa baja en sodio", 3)
    assert all(it["group"] == "soup" and "low_sodium" in it["tags"] for it in top)


def test_suggestions_stay_close_to_the_product():
    alts = catalog.suggest("SOU-001")
    assert alts and all("chicken" in a["name"].lower() for a in alts)


def test_real_listing_of_a_demo_product_is_deduped():
    names = [it["name"].lower() for it in catalog.items.values()
             if it["group"] == "bread" and it["merchant"] == "corner_market"]
    assert sum(n.startswith("nature's own honey wheat bread") for n in names) == 1


def test_every_item_has_a_mandate_category():
    allowed = {"grocery", "pharmacy", "household", "gift_card", "prepaid_card"}
    assert all(it.get("mandate_category") in allowed for it in catalog.items.values())


def test_gift_and_prepaid_cards_never_map_to_grocery_or_pharmacy():
    for it in catalog.items.values():
        if it["category"] in ("gift_card", "prepaid_card"):
            assert it["mandate_category"] == it["category"]
    assert catalog.items["RX-001"]["mandate_category"] == "pharmacy"
    assert catalog.items["BAK-001"]["mandate_category"] == "grocery"


@pytest.mark.parametrize("q", ["my blood pressure medicine", "blood pressure pills", "medicina de la presion",
                               "meri bp ki dawai"])
def test_generic_medicine_words_do_not_pull_in_otc_items(q):
    assert [i["sku"] for i in catalog.search(q)] == ["RX-001"]


def test_generic_words_alone_still_list_medicines():
    found = catalog.search("medicine")
    assert found and all(i["category"] == "otc_medicine" for i in found)


def test_unknown_medicine_is_not_answered_with_another():
    assert catalog.search("cough medicine") == []


def test_specific_medicine_words_still_rank():
    assert all(i["group"] == "allergy" for i in catalog.search("allergy medicine", 5))
    assert all(i["group"] == "pain_relief" for i in catalog.search("dard ki dawai", 5))


# stores
def test_items_belong_to_their_store():
    assert catalog.items["RX-001"]["merchant"] == "parkside_pharmacy"
    assert catalog.items["OTC-001"]["merchant"] == "parkside_pharmacy"
    assert catalog.items["BAK-001"]["merchant"] == "corner_market"
    assert catalog.items["HOM-005"]["merchant"] == "main_street_home"
    assert catalog.items["GFT-001"]["merchant"] == "quickgift_cards"  # only the blocked shop sells them
    assert catalog.items["PPD-001"]["merchant"] == "quickgift_cards"
    parkside_grocery = [it for it in catalog.items.values()
                        if it["merchant"] == "parkside_pharmacy" and it["mandate_category"] == "grocery"]
    assert 15 <= len(parkside_grocery) <= 25
    assert not any(it["brand"] == "Kroger" for it in parkside_grocery)


def test_every_result_names_its_store_and_where_else_it_is_sold():
    top = catalog.search("bread", 1)[0]
    assert top["sku"] == "BAK-001" and top["store"] == "Corner Market" and top["merchant"] == "corner_market"
    assert top["elsewhere"] == [{"merchant": "parkside_pharmacy", "store": "Parkside Pharmacy", "sku": "PK-BAK-001",
                                 "price": catalog.items["PK-BAK-001"]["price"]}]
    assert all({"merchant", "store", "elsewhere", "usual"} <= set(it) for it in catalog.search("milk"))


def test_one_product_at_two_stores_is_one_result_at_the_better_price():
    skus = [it["sku"] for it in catalog.search("ensure vanilla", 20)]
    assert not ({"NUT-001", "PK-NUT-001"} <= set(skus))
    shake = next(it for it in catalog.search("ensure vanilla", 20) if it["sku"] in ("NUT-001", "PK-NUT-001"))
    assert shake["price"] <= shake["elsewhere"][0]["price"]


def test_store_filter_by_id_or_name():
    for store in ("parkside_pharmacy", "Parkside Pharmacy", "parkside"):
        found = catalog.search("bread", 10, store)
        assert found and all(it["merchant"] == "parkside_pharmacy" for it in found)
        assert found[0]["usual"]  # her usual bread, at Parkside's price
    assert catalog.search("bread", 10, "main_street_home") == []
    for alias in ("home", "Main Street", "main street home"):
        assert catalog.store_id(alias) == "main_street_home"
    assert catalog.store_id("power") == "peachtree_power" and catalog.store_id("market") == "corner_market"
    assert catalog.search("paper towels", 3, "home")


def test_household_items_are_found_in_three_languages():
    for q in ("paper towels", "toilet paper", "pilas", "detergente", "bolsas de basura"):
        found = catalog.search(q, 3)
        assert found and all(it["merchant"] == "main_street_home" for it in found), q


def test_usual_first_then_price_and_no_brand_takes_the_whole_list():
    found = catalog.search("milk", 6)
    prices = [it["price"] for it in found if not it["usual"]]
    brands = [it["brand"] for it in found[:4]]
    assert max(brands.count(b) for b in set(brands)) <= 2
    assert found[0]["usual"] or prices[0] == min(prices[:3])


def test_suggestion_at_another_store_says_so():
    alts = catalog.suggest("NUT-001")
    parkside = next((a for a in alts if a["sku"] == "PK-NUT-001"), None)
    assert parkside and "at Parkside Pharmacy" in parkside["reasons"]
