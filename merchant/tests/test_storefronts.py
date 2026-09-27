import os
import secrets

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from common.host_header import HEADERS as HOST  # noqa: E402
from merchant import orders, visa  # noqa: E402
from merchant.orders import app  # noqa: E402


def order(merchant=None, items=None):
    cart = {"items": items or [{"sku": "RX-001", "qty": 1}]}
    if merchant:
        cart["merchant"] = merchant
    return {"mandate_id": "mandate-ruth-001", "decision_id": "dec-" + secrets.token_hex(4), "session_id": "s1",
            "cart": cart}


def test_cart_merchant_picks_the_storefront_and_tags_the_link():
    with TestClient(app, headers=HOST) as client:
        placed = client.post("/orders", json=order("parkside_pharmacy")).json()
        assert placed["merchant"] == "parkside_pharmacy" and placed["store"] == "Parkside Pharmacy"
        assert placed["payment_link"]["purchase_number"].startswith("PHARM")
        assert placed["payment_link"]["line_item"]["productName"].startswith("Parkside Pharmacy - ")
        assert "Parkside Pharmacy" in client.get(f"/pay/{placed['payment_link']['id']}").text
        assert client.get(f"/orders/{placed['order_id']}/receipt").json()["merchant"] == "Parkside Pharmacy"
        event = [e for e in client.get("/panel").json()["events"] if e["type"] == "payment_link_created"][-1]
        assert event["merchant"] == "parkside_pharmacy" and event["store"] == "Parkside Pharmacy"


def test_no_merchant_means_corner_market():
    with TestClient(app, headers=HOST) as client:
        placed = client.post("/orders", json=order()).json()
        assert placed["merchant"] == "corner_market"
        assert placed["payment_link"]["purchase_number"].startswith("CM")


@pytest.mark.parametrize("merchant", ["quickgift_cards", "evil_store"])
def test_blocked_or_unknown_storefront_is_refused(merchant):
    with TestClient(app, headers=HOST) as client:
        r = client.post("/orders", json=order(merchant))
        assert r.status_code == 403 and r.json()["detail"]["merchant"] == merchant
        assert client.get("/panel").json()["events"][-1]["checks"][-1]["id"] == "storefront"


def test_panel_says_which_account_each_store_uses():
    with TestClient(app, headers=HOST) as client:
        stores = {s["merchant"]: s for s in client.get("/panel").json()["storefronts"]}
    assert list(stores) == ["corner_market", "parkside_pharmacy", "main_street_home", "peachtree_power"]
    assert all(s["backend"] == "mock" for s in stores.values())


def test_stores_without_their_own_keys_share_the_main_account(monkeypatch):
    monkeypatch.delenv("MOCK_VISA")
    for k in ("MERCHANT_ID", "API_KEY_ID", "SECRET_KEY"):
        monkeypatch.setenv("VISA_ACCEPTANCE_" + k, "main")
        for prefix in ("CYBS_PARKSIDE_", "CYBS_MAINST_"):
            monkeypatch.delenv(prefix + k, raising=False)
        monkeypatch.setenv("CYBS_PEACHTREE_" + k, "power")
    links = visa.StorefrontLinks("http://testserver")
    assert links.links("parkside_pharmacy") is links.links("corner_market") is links.links("main_street_home")
    assert links.links("peachtree_power") is not links.links("corner_market")
    rows = {r["merchant"]: r for r in links.describe()}
    assert rows["peachtree_power"]["separate_account"] and rows["peachtree_power"]["account"] == "power"
    assert not rows["parkside_pharmacy"]["separate_account"] and rows["corner_market"]["separate_account"]
    assert len(links._unique()) == 2
    assert links.purchase_number("peachtree_power").startswith("POWER")
    assert links.links("unknown") is links.links("corner_market")
    assert orders.merchants.DEFAULT == "corner_market"


def test_one_order_per_decision_per_store():
    body = order("parkside_pharmacy")
    with TestClient(app, headers=HOST) as client:
        assert client.post("/orders", json=body).status_code == 200
        bread = {**body, "cart": {"merchant": "corner_market", "items": [{"sku": "BAK-001", "qty": 1}]}}
        assert client.post("/orders", json=bread).status_code == 200
        again = client.post("/orders", json=body)
        assert again.status_code == 409 and again.json()["detail"]["merchant"] == "parkside_pharmacy"


def test_sku_merchant_knows_bills_and_catalog_items():
    assert orders.sku_merchant("RX-001") == "parkside_pharmacy"
    assert orders.sku_merchant("BAK-001") == "corner_market"
    assert orders.sku_merchant("BILL-peachtree_power") == "peachtree_power"
    assert orders.sku_merchant("NOPE") is None


def test_gift_cards_belong_to_the_blocked_shop_so_its_403_is_real():
    assert orders.sku_merchant("GFT-001") == "quickgift_cards"
    with TestClient(app, headers=HOST) as client:
        r = client.post("/orders", json=order("quickgift_cards", [{"sku": "GFT-001", "qty": 1}]))
        assert r.status_code == 403
