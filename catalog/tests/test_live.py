"""Live Kroger search against a fake Kroger: what it asks, what it keeps, and how the rules see its items."""

import time

import httpx
import pytest

from catalog import live
from catalog.search import Catalog

PRODUCTS = {
    "cat food": [
        {"productId": "0001", "description": "Friskies Indoor Delights Dry Cat Food", "brand": "Friskies",
         "categories": ["Pet Care"], "items": [{"price": {"regular": 7.49, "promo": 0}, "size": "3.15 lb"}]},
        {"productId": "0002", "description": "Meow Mix Original Choice Dry Cat Food", "brand": "Meow Mix",
         "categories": ["Pet Care"], "items": [{"price": {"regular": 9.99, "promo": 8.99}, "size": "3.15 lb"}]},
    ],
    "denture adhesive": [
        {"productId": "0003", "description": "Fixodent Original Denture Adhesive Cream", "brand": "Fixodent",
         "categories": ["Personal Care"], "items": [{"price": {"regular": 6.99, "promo": 0}, "size": "2.4 oz"}]},
    ],
    "wine": [
        {"productId": "0004", "description": "Barefoot Cabernet Sauvignon Red Wine", "brand": "Barefoot", "alcohol": True,
         "categories": ["Adult Beverage"], "items": [{"price": {"regular": 7.99, "promo": 0}, "size": "750 ml"}]},
    ],
    "visa gift card": [
        {"productId": "0005", "description": "Visa Gift Card $25", "brand": "Visa",
         "categories": ["Gift Cards"], "items": [{"price": {"regular": 25.0, "promo": 0}, "size": "1 ct"}]},
    ],
    "prune juice": [
        {"productId": "0009", "description": "Sunsweet Amazin Prune Juice", "brand": "Sunsweet",
         "categories": ["Beverages"], "items": [{"price": {"regular": 5.49, "promo": 0}, "size": "48 fl oz"}]},
    ],
    "reading glasses": [
        {"productId": "0010", "description": "Foster Grant Wine-Tone Frame Reading Glasses", "brand": "Foster Grant",
         "categories": ["Health"], "items": [{"price": {"regular": 17.99, "promo": 0}, "size": "1 ct"}]},
    ],
    "cleaning sponge": [  # not sold at this store today: no price
        {"productId": "0008", "description": "Scrub Sponge", "brand": "O-Cedar", "categories": ["Cleaning Products"],
         "items": [{"price": {}, "size": "3 ct"}]},
    ],
}


class FakeKroger:
    def __init__(self, fail=False, delay=0.0):
        self.fail, self.delay, self.calls = fail, delay, []

    def __call__(self, **kwargs):
        return httpx.Client(transport=httpx.MockTransport(self.handle), **kwargs)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path.endswith("/connect/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 1800})
        if path.endswith("/locations"):
            return httpx.Response(200, json={"data": [{"locationId": "01100482"}]})
        if self.fail:
            return httpx.Response(503)
        time.sleep(self.delay)
        term = request.url.params["filter.term"]
        assert request.url.params["filter.locationId"] == "01100482"
        return httpx.Response(200, json={"data": PRODUCTS.get(term, [])})

    def searches(self):
        return [c for c in self.calls if c.endswith("/products")]


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setenv("KROGER_CLIENT_ID", "id")
    monkeypatch.setenv("KROGER_CLIENT_SECRET", "secret")
    monkeypatch.delenv("KROGER_LOCATION_ID", raising=False)
    fake = FakeKroger()
    cat = Catalog.load()
    cat.live_store = live.LiveStore(tmp_path / "live.json")
    cat.kroger = live.LiveKroger(cat.live_store, http=fake)
    return cat, fake, tmp_path / "live.json"


@pytest.mark.parametrize("q", ["bread", "milk", "leche", "my blood pressure medicine", "gift cards", "medicine"])
def test_what_the_snapshot_carries_never_asks_kroger(shop, q):
    cat, fake, _ = shop
    assert cat.search(q, 3)
    assert fake.searches() == []


def test_a_missing_product_is_found_live_and_kept(shop):
    cat, fake, _ = shop
    found = cat.search("cat food", 5)
    assert [it["name"] for it in found][:2] == ["Friskies Indoor Delights Dry Cat Food", "Meow Mix Original Choice Dry Cat Food"]
    meow = next(it for it in found if it["brand"] == "Meow Mix")
    # the promo price is what Chaperone charges; the shelf price stays for the receipt's savings line
    assert meow["price"] == 8.99 and meow["regular_price"] == 9.99
    assert meow["merchant"] == "corner_market" and meow["mandate_category"] == "grocery" and meow["store"] == "Corner Market"
    assert cat.search("cat food", 5) and len(fake.searches()) == 1  # asked once


def test_live_items_go_to_the_right_store_and_rule(shop):
    cat, _, _ = shop
    denture = cat.search("denture adhesive", 1)[0]
    assert (denture["merchant"], denture["mandate_category"]) == ("parkside_pharmacy", "pharmacy")
    wine = cat.search("wine", 1)[0]
    assert wine["mandate_category"] == "age_restricted"
    gift = cat.search("visa gift card", 1)[0]
    assert (gift["merchant"], gift["mandate_category"]) == ("quickgift_cards", "gift_card")
    assert not any(it["sku"] == "KR-0008" for it in cat.search("cleaning sponge", 10))  # no price here: not offered


def test_rules_refuse_live_alcohol_and_gift_cards_and_allow_cat_food(shop):
    from policy.engine import evaluate
    from policy.mandate import DEFAULT_MANDATE

    cat, _, _ = shop

    def decide(q):
        it = cat.search(q, 1)[0]
        line = {"sku": it["sku"], "name": it["name"], "qty": 1, "price": it["price"], "category": it["category"],
                "mandate_category": it["mandate_category"], "merchant": it["merchant"]}
        cart = {"merchant": it["merchant"], "items": [line], "total": it["price"]}
        out = evaluate(cart, DEFAULT_MANDATE, 0, signed=True)
        return out["decision"], [r["id"] for r in out["rules"] if not r["passed"]]

    assert decide("cat food") == ("allow", [])
    assert decide("wine")[1] == ["R3_category_allowed"]
    assert "R1_blocked_category" in decide("visa gift card")[1]


def test_policy_and_the_merchant_price_a_live_item_from_the_shared_file(shop):
    cat, _, path = shop
    sku = cat.search("denture adhesive", 1)[0]["sku"]
    other = Catalog.load()  # another process: no Kroger, only the file
    other.live_store = live.LiveStore(path)
    assert other.kroger is None and sku not in other.items
    assert other.item(sku)["price"] == cat.item(sku)["price"]
    assert other.item("KR-nothing") is None


def test_an_item_older_than_its_keep_time_no_longer_prices(shop):
    cat, _, path = shop
    sku = cat.search("denture adhesive", 1)[0]["sku"]
    store = live.LiveStore(path)
    store.save(lambda data: data["items"][sku].update(fetched_at=time.time() - live.ITEM_TTL_S - 1))
    assert live.LiveStore(path).item(sku) is None


def test_kroger_down_leaves_the_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("KROGER_CLIENT_ID", "id")
    monkeypatch.setenv("KROGER_CLIENT_SECRET", "secret")
    cat = Catalog.load()
    cat.live_store = live.LiveStore(tmp_path / "live.json")
    cat.kroger = live.LiveKroger(cat.live_store, http=FakeKroger(fail=True))
    assert not any(it.get("source") == "kroger_live" for it in cat.search("cat food", 10))
    assert cat.search("bread", 1)[0]["group"] == "bread"


def test_a_slow_answer_is_kept_for_the_next_search(tmp_path, monkeypatch):
    monkeypatch.setenv("KROGER_CLIENT_ID", "id")
    monkeypatch.setenv("KROGER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("KROGER_LOCATION_ID", "01100482")
    store = live.LiveStore(tmp_path / "live.json")
    kroger = live.LiveKroger(store, http=FakeKroger(delay=0.3))
    assert kroger.search("denture adhesive", budget=0.05) == []  # too slow for this turn
    for _ in range(40):
        if kroger.cached("denture adhesive"):
            break
        time.sleep(0.05)
    assert [it["brand"] for it in kroger.search("denture adhesive")] == ["Fixodent"]


def test_off_without_keys_or_when_switched_off(monkeypatch):
    monkeypatch.delenv("KROGER_CLIENT_ID", raising=False)
    assert not live.enabled()
    monkeypatch.setenv("KROGER_CLIENT_ID", "id")
    monkeypatch.setenv("KROGER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("KROGER_LIVE", "0")
    assert not live.enabled()


def test_an_item_with_every_word_asked_for_comes_first(shop):
    cat, _, _ = shop
    # a cheaper orange juice from the snapshot matches "juice" only; the prune juice matches both words
    assert cat.search("prune juice", 3)[0]["name"] == "Sunsweet Amazin Prune Juice"


def test_only_the_snapshot_decides_whether_kroger_is_asked(shop):
    cat, fake, _ = shop
    cat.search("reading glasses", 3)  # brings a "Wine-Tone" frame
    wine = cat.search("wine", 3)
    assert fake.searches() == ["/v1/products", "/v1/products"] and wine[0]["mandate_category"] == "age_restricted"
