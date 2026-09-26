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
