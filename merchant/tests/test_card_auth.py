import os

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from merchant import card_auth, orders  # noqa: E402
from merchant.card_auth import AuthResult  # noqa: E402

DEMO = {
    "mandate_id": "mandate-ruth-001",
    "decision_id": "dec-card-001",
    "session_id": "s-card",
    "cart": {"items": [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 1}]},
}
FORM = {"number": "4111 1111 1111 1111", "exp_month": "12", "exp_year": "2030", "cvv": "123"}


@pytest.fixture
def card_auth_on(monkeypatch):
    monkeypatch.setattr(orders, "CARD_AUTH", True)
    calls = []

    def fake(result):
        def _authorize(*args):
            calls.append(args)
            return result
        monkeypatch.setattr(card_auth, "authorize", _authorize)
    return fake, calls


def test_checkout_is_off_by_default():
    with TestClient(orders.app) as client:
        order = client.post("/orders", json=DEMO).json()
        assert order["checkout_url"] is None
        assert client.get(f"/checkout/{order['order_id']}").status_code == 404


def test_authorized_card_marks_order_paid(card_auth_on):
    fake, calls = card_auth_on
    fake(AuthResult(True, "AUTHORIZED", "7903900000000000000001", "ours", approval_code="831000", card_brand="Visa"))
    with TestClient(orders.app) as client:
        order = client.post("/orders", json=DEMO).json()
        assert order["checkout_url"].endswith(f"/checkout/{order['order_id']}")
        assert "Pay $11.49" in client.get(f"/checkout/{order['order_id']}").text

        page = client.post(f"/checkout/{order['order_id']}", data=FORM)
        assert "Authorized by the Cybersource sandbox" in page.text
        after = client.get(f"/orders/{order['order_id']}").json()
        assert after["status"] == "paid" and after["paid_via"] == "card_auth:ours"
        assert after["card_auth"]["request_id"] == "7903900000000000000001"
        assert calls[0][:2] == ("11.49", order["order_id"])  # amount comes from our catalog, not the form
        events = [e["type"] for e in client.get("/panel").json()["events"]]
        assert events[-2:] == ["card_authorized", "paid"]


def test_failed_authorization_keeps_order_unpaid_and_is_not_retried(card_auth_on):
    fake, calls = card_auth_on
    fake(AuthResult(False, "SERVER_ERROR", "7903900000000000000002", "ours", reason="Error - General system failure."))
    with TestClient(orders.app) as client:
        order = client.post("/orders", json=DEMO).json()
        page = client.post(f"/checkout/{order['order_id']}", data=FORM)
        assert "Host can mark the order paid" in page.text
        assert len(calls) == 1
        assert client.get(f"/orders/{order['order_id']}").json()["status"] == "awaiting_payment"
        assert client.get("/panel").json()["events"][-1]["type"] == "card_auth_failed"


def test_paid_order_never_authorizes_twice(card_auth_on):
    fake, calls = card_auth_on
    fake(AuthResult(True, "AUTHORIZED", "7903900000000000000003", "ours"))
    with TestClient(orders.app) as client:
        order = client.post("/orders", json=DEMO).json()
        client.post(f"/checkout/{order['order_id']}", data=FORM)
        client.post(f"/checkout/{order['order_id']}", data=FORM)
        assert len(calls) == 1


def test_non_test_cards_are_refused_before_any_request(monkeypatch):
    for var in ("VISA_ACCEPTANCE_MERCHANT_ID", "VISA_ACCEPTANCE_API_KEY_ID", "VISA_ACCEPTANCE_SECRET_KEY"):
        monkeypatch.setenv(var, "dummy")

    def no_network(*args, **kwargs):
        raise AssertionError("must not call Cybersource for a non-test card")

    monkeypatch.setattr(card_auth, "signed_request", no_network)
    result = card_auth.authorize("11.49", "ord_x", "4242 4242 4242 4242", "12", "2030", "123")
    assert result.status == "REFUSED_LOCALLY" and not result.ok


def test_override_merchant_wins_when_fully_set(monkeypatch):
    for var in ("VISA_ACCEPTANCE_MERCHANT_ID", "VISA_ACCEPTANCE_API_KEY_ID", "VISA_ACCEPTANCE_SECRET_KEY"):
        monkeypatch.setenv(var, "ours")
    monkeypatch.setenv("CARD_AUTH_MERCHANT_ID", "second")
    monkeypatch.setenv("CARD_AUTH_API_KEY_ID", "k")
    monkeypatch.delenv("CARD_AUTH_SECRET_KEY", raising=False)
    assert card_auth.auth_creds()[1] == "ours"  # partial override is ignored
    monkeypatch.setenv("CARD_AUTH_SECRET_KEY", "s")
    creds, which = card_auth.auth_creds()
    assert which == "override" and creds.merchant_id == "second"
