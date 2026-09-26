import os

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from merchant.orders import app  # noqa: E402
from merchant.visa import new_purchase_number  # noqa: E402

DEMO = {
    "mandate_id": "mandate-ruth-001",
    "decision_id": "dec-001",
    "session_id": "s1",
    "cart": {"items": [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 1}]},
}


def test_demo_order_prices_from_catalog_and_pays_on_mock_page():
    with TestClient(app) as client:
        r = client.post("/orders", json=DEMO)
        assert r.status_code == 200, r.text
        order = r.json()
        assert order["amount"] == "11.49"
        assert order["status"] == "awaiting_payment"
        link = order["payment_link"]
        assert link["backend"] == "mock" and link["url"].endswith(f"/pay/{link['id']}")

        page = client.get(f"/pay/{link['id']}")
        assert "Pay $11.49" in page.text

        client.post(f"/pay/{link['id']}")
        assert client.get(f"/orders/{order['order_id']}").json()["status"] == "paid"
        events = [e["type"] for e in client.get("/panel").json()["events"]]
        assert events[-3:] == ["signature_verified", "payment_link_created", "paid"]


def test_agent_supplied_prices_are_ignored():
    body = {**DEMO, "cart": {"items": [{"sku": "NUT-002", "qty": 1, "price": 0.01}]}}
    with TestClient(app) as client:
        assert client.post("/orders", json=body).json()["amount"] == "52.00"


def test_callback_fallback_marks_paid_once():
    with TestClient(app) as client:
        order_id = client.post("/orders", json=DEMO).json()["order_id"]
        first = client.post(f"/orders/{order_id}/paid").json()
        second = client.post(f"/orders/{order_id}/paid?via=webhook").json()
        assert first["paid_at"] == second["paid_at"] and second["paid_via"] == "callback"


def test_rejects_unknown_sku_bad_qty_and_empty_cart():
    with TestClient(app) as client:
        for items in ([{"sku": "NOPE", "qty": 1}], [{"sku": "BAK-001", "qty": 0}], []):
            assert client.post("/orders", json={**DEMO, "cart": {"items": items}}).status_code == 422


def test_purchase_number_fits_visa_rules():
    for _ in range(50):
        pn = new_purchase_number()
        assert pn.isalnum() and len(pn) < 20


def test_reset_clears_orders_and_panel():
    with TestClient(app) as client:
        client.post("/orders", json=DEMO)
        assert client.post("/reset").json()["ok"]
        assert client.get("/orders").json() == []
        assert client.get("/panel").json()["events"] == []


def test_events_follow_the_ledger_contract():
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json").read_text())
    with TestClient(app) as client:
        client.post("/reset")
        order = client.post("/orders", json=DEMO).json()
        client.post(f"/orders/{order['order_id']}/paid")
        for event in client.get("/panel").json()["events"]:
            jsonschema.validate(event, schema)
            assert event["source"] == "merchant" and isinstance(event["t"], int)
