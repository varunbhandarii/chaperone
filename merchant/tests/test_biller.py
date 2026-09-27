import os
import secrets

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from common.host_header import HEADERS as HOST  # noqa: E402
from merchant import biller  # noqa: E402
from merchant.orders import app  # noqa: E402

ACCOUNT = "/billers/peachtree_power/accounts/PP-2231-0098"


def bill_order(merchant="peachtree_power", qty=1):
    return {"mandate_id": "mandate-ruth-001", "decision_id": "dec-" + secrets.token_hex(4), "session_id": "s1",
            "cart": {"merchant": merchant, "items": [{"sku": "BILL-peachtree_power", "qty": qty}]}}


@pytest.fixture
def client():
    with TestClient(app, headers=HOST) as c:
        c.post("/reset")
        yield c
        c.post("/reset")


def test_account_facts_have_the_bill_fields(client):
    facts = client.get(ACCOUNT).json()
    assert {k: facts[k] for k in ("biller", "account_ref", "balance_due", "due_date", "past_due", "autopay",
                                  "last_payment", "disconnect_notice")} == {
        "biller": "Peachtree Power", "account_ref": "PP-2231-0098", "balance_due": "86.40", "due_date": "2026-10-15",
        "past_due": False, "autopay": False, "last_payment": {"amount": "91.12", "at": "2026-09-12"},
        "disconnect_notice": False}
    assert facts["say"] == ("Your Peachtree Power bill is $86.40 and due October 15. "
                            "It is not past due, and there is no disconnect notice.")
    assert "15 de octubre" in client.get(ACCOUNT + "?lang=es").json()["say"]
    event = client.get("/panel").json()["events"][-1]
    assert event["type"] == "bill_checked" and event["balance_due"] == "86.40" and event["past_due"] is False


def test_unknown_biller_or_account_is_404(client):
    assert client.get("/billers/georgia_gas/accounts/PP-2231-0098").status_code == 404
    assert client.get("/billers/peachtree_power/accounts/PP-0000-0000").status_code == 404


def test_paying_the_bill_prices_it_from_the_balance_and_clears_it(client):
    order = client.post("/orders", json=bill_order()).json()
    assert order["amount"] == "86.40" and order["store"] == "Peachtree Power"
    assert order["lines"][0]["name"] == "Peachtree Power bill PP-...0098"
    assert order["payment_link"]["line_item"]["productName"] == "Peachtree Power bill PP-...0098"
    assert order["payment_link"]["purchase_number"].startswith("POWER")
    assert order["pickup_code"] is None and order["loyalty_points"] is None
    client.post(f"/orders/{order['order_id']}/paid")
    paid = client.get(f"/orders/{order['order_id']}").json()
    assert paid["status"] == "paid" and paid["fulfilment"] == "paid"
    facts = client.get(ACCOUNT).json()
    assert facts["balance_due"] == "0.00" and facts["last_payment"]["amount"] == "86.40"
    assert facts["say"].startswith("Your Peachtree Power bill is paid.")
    assert client.post("/orders", json=bill_order()).status_code == 409  # nothing due now
    assert client.get(f"/orders/{order['order_id']}/receipt").json()["pickup"] is None


def test_reset_restores_the_bill(client):
    order = client.post("/orders", json=bill_order()).json()
    client.post(f"/orders/{order['order_id']}/paid")
    client.post("/reset")
    assert client.get(ACCOUNT).json()["balance_due"] == "86.40"


@pytest.mark.parametrize("merchant,qty", [("corner_market", 1), ("peachtree_power", 2)])
def test_bill_only_at_its_biller_and_once(client, merchant, qty):
    assert client.post("/orders", json=bill_order(merchant, qty)).status_code == 422


def test_masked_account_is_ascii():
    assert biller.masked("PP-2231-0098") == "PP-...0098"


def test_price_lookups_post_no_bill_checked(client):
    client.get(ACCOUNT + "?purpose=price")
    assert not [e for e in client.get("/panel").json()["events"] if e["type"] == "bill_checked"]
    client.get(ACCOUNT + "?session_id=s1")
    checked = [e for e in client.get("/panel").json()["events"] if e["type"] == "bill_checked"]
    assert len(checked) == 1 and checked[0]["session_id"] == "s1"


def test_bill_uses_the_mandates_account(client, monkeypatch):
    biller.BILLERS["peachtree_power"]["accounts"]["PP-7777-0001"] = {
        "balance_due": "42.10", "due_date": "2026-10-20", "past_due": False, "autopay": False,
        "last_payment": None, "disconnect_notice": False}
    from merchant import orders

    async def mandate_ref(biller_id, mandate_id):
        return "PP-7777-0001"
    monkeypatch.setattr(orders, "_mandate_account_ref", mandate_ref)
    order = client.post("/orders", json=bill_order()).json()
    assert order["amount"] == "42.10" and order["lines"][0]["name"] == "Peachtree Power bill PP-...0001"
    named = bill_order()
    named["cart"]["items"][0]["account_ref"] = "PP-2231-0098"  # a line that names its account wins
    assert client.post("/orders", json=named).json()["amount"] == "86.40"


def test_receipt_names_the_store_and_a_bill_has_no_pickup(client):
    order = client.post("/orders", json=bill_order()).json()
    receipt = client.get(f"/orders/{order['order_id']}/receipt").json()
    assert receipt["store"] == "Peachtree Power" and receipt["kind"] == "biller"
    assert receipt["pickup"] is None and receipt["pickup_code"] is None
