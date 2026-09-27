import os
import secrets

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from merchant.orders import app  # noqa: E402
from merchant.visa import new_purchase_number  # noqa: E402
from common.host_header import HEADERS as HOST  # noqa: E402

DEMO = {
    "mandate_id": "mandate-ruth-001",
    "decision_id": "dec-001",
    "session_id": "s1",
    "cart": {"items": [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 1}]},
}


def demo(**changes):
    """DEMO under a fresh decision id: the merchant takes one order per decision."""
    return {**DEMO, "decision_id": "dec-" + secrets.token_hex(4), **changes}


def test_demo_order_prices_from_catalog_and_pays_on_mock_page():
    with TestClient(app, headers=HOST) as client:
        r = client.post("/orders", json=demo())
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
    body = {**demo(), "cart": {"items": [{"sku": "NUT-002", "qty": 1, "price": 0.01}]}}
    with TestClient(app, headers=HOST) as client:
        assert client.post("/orders", json=body).json()["amount"] == "52.00"


def test_callback_fallback_marks_paid_once():
    with TestClient(app, headers=HOST) as client:
        order_id = client.post("/orders", json=demo()).json()["order_id"]
        first = client.post(f"/orders/{order_id}/paid").json()
        second = client.post(f"/orders/{order_id}/paid?via=webhook").json()
        assert first["paid_at"] == second["paid_at"] and second["paid_via"] == "callback"


def test_rejects_unknown_sku_bad_qty_and_empty_cart():
    with TestClient(app, headers=HOST) as client:
        for items in ([{"sku": "NOPE", "qty": 1}], [{"sku": "BAK-001", "qty": 0}], []):
            assert client.post("/orders", json={**demo(), "cart": {"items": items}}).status_code == 422


def test_purchase_number_fits_visa_rules():
    for _ in range(50):
        pn = new_purchase_number()
        assert pn.isalnum() and len(pn) < 20


def test_reset_clears_orders_and_panel():
    with TestClient(app, headers=HOST) as client:
        client.post("/orders", json=demo())
        assert client.post("/reset").json()["ok"]
        assert client.get("/orders").json() == []
        assert client.get("/panel").json()["events"] == []


def test_events_follow_the_ledger_contract():
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json").read_text())
    with TestClient(app, headers=HOST) as client:
        client.post("/reset")
        order = client.post("/orders", json=demo()).json()
        client.post(f"/orders/{order['order_id']}/paid")
        for event in client.get("/panel").json()["events"]:
            jsonschema.validate(event, schema)
            assert event["source"] == "merchant" and isinstance(event["t"], int)


def test_one_decision_buys_once():
    body = demo()
    with TestClient(app, headers=HOST) as client:
        first = client.post("/orders", json=body)
        assert first.status_code == 200
        again = client.post("/orders", json=body)
        assert again.status_code == 409 and again.json()["detail"]["order_id"] == first.json()["order_id"]
        client.post("/reset")  # a reset does not free the decision either
        assert client.post("/orders", json=body).status_code == 409
        rejected = client.get("/panel").json()["events"][-1]
        assert rejected["type"] == "signature_rejected" and "already has order" in rejected["checks"][-1]["detail"]


def test_a_failed_payment_link_frees_the_decision(monkeypatch):
    from merchant import orders

    body = demo()
    with TestClient(app, headers=HOST) as client:
        async def down(**kwargs):
            raise RuntimeError("visa down")
        monkeypatch.setattr(orders.payment_links, "create", down)
        assert client.post("/orders", json=body).status_code == 502
        monkeypatch.undo()
        assert client.post("/orders", json=body).status_code == 200


def test_receipt_has_the_d3_shape(monkeypatch):
    monkeypatch.setenv("TUNNEL_HOST", "chaperone-demo.ngrok.app")
    with TestClient(app, headers=HOST) as client:
        order = client.post("/orders", json=demo(cart={"items": [{"sku": "BAK-001", "qty": 2}, {"sku": "RX-001", "qty": 1}]},
                                                 lang="hi")).json()
        unpaid = client.get(f"/orders/{order['order_id']}/receipt").json()
        assert unpaid["status"] == "awaiting_payment" and unpaid["paid_at"] is None
        client.post(f"/orders/{order['order_id']}/paid")
        r = client.get(f"/orders/{order['order_id']}/receipt").json()
        assert set(r) >= {"merchant", "items", "total", "pickup", "order_id", "decision_id", "paid_at", "session_url", "lang"}
        assert r["merchant"] == "Corner Market" and r["pickup"] == "after 3pm" and r["lang"] == "hi"
        assert r["items"][0] == {"name": r["items"][0]["name"], "qty": 2, "price": "6.98", "unit_price": "3.49",
                                 "sku": "BAK-001"}
        assert r["total"] == "14.98" and r["paid_at"].endswith("+00:00")
        assert r["session_url"] == "https://chaperone-demo.ngrok.app/s/s1"
        assert client.get(f"/orders/{order['order_id']}/receipt?lang=es").json()["lang"] == "es"
        assert client.get("/orders/ord_nope/receipt").status_code == 404


def test_receipt_has_no_lan_url_without_a_tunnel(monkeypatch):
    monkeypatch.delenv("TUNNEL_HOST", raising=False)
    with TestClient(app, headers=HOST) as client:
        order = client.post("/orders", json=demo()).json()
        assert client.get(f"/orders/{order['order_id']}/receipt").json()["session_url"] is None
    monkeypatch.setenv("TUNNEL_HOST", "http://chaperone-demo.ngrok.app/")
    from merchant.orders import session_url
    assert session_url("s1") == "https://chaperone-demo.ngrok.app/s/s1"


def test_reset_and_paid_need_the_host_header():
    with TestClient(app, headers=HOST) as client:
        order_id = client.post("/orders", json=demo()).json()["order_id"]
    bare = TestClient(app)
    assert bare.post("/reset").status_code == 403
    assert bare.post(f"/orders/{order_id}/paid").status_code == 403
    assert bare.get(f"/orders/{order_id}").json()["status"] == "awaiting_payment"


def test_tests_run_on_the_mock_whatever_the_env_file_says():
    """Root conftest.py pins MOCK_VISA=1, so no test makes a real Visa link even when .env has MOCK_VISA=0."""
    from merchant import orders

    assert os.environ["MOCK_VISA"] == "1"
    assert {s["backend"] for s in orders.storefront_links.describe()} == {"mock"}
