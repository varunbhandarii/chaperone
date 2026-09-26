import base64
import os
import secrets
import time

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from merchant.orders import app as merchant_app  # noqa: E402
from relay import host, ledger  # noqa: E402,F401

ORDER = {"mandate_id": "m_ruth_2026_09", "session_id": "s1",
         "cart": {"items": [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 1}]}}


@pytest.fixture
def merchant():
    with TestClient(merchant_app) as m:
        m.post("/reset")
        yield m


@pytest.fixture
def client(tmp_path, monkeypatch, merchant):
    """The relay, with its outgoing HTTP calls to the merchant served in-process by the real merchant app."""
    monkeypatch.setattr(ledger, "LEDGER", ledger.Ledger(tmp_path / "live.jsonl"))
    monkeypatch.setenv("MERCHANT_URL", "http://merchant")
    monkeypatch.setenv("POLICY_URL", "http://127.0.0.1:9")
    real = httpx.AsyncClient

    def routed(**kwargs):
        kwargs.pop("transport", None)
        transport = httpx.ASGITransport(app=merchant_app)
        return real(transport=transport, base_url="http://merchant", **kwargs)
    monkeypatch.setattr(host.httpx, "AsyncClient", routed)
    return TestClient(ledger.app)


def place(merchant):
    return merchant.post("/orders", json={**ORDER, "decision_id": "d_" + secrets.token_hex(6)}).json()


def test_confirm_payment_pays_the_latest_unpaid_order_through_the_signed_webhook(client, merchant, monkeypatch):
    monkeypatch.setenv("CYBS_WEBHOOK_KEY_ID", "host-test")
    monkeypatch.setenv("CYBS_WEBHOOK_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    order = place(merchant)
    r = client.post("/host/api/confirm-payment")
    assert r.status_code == 200, r.text
    assert r.json()["order_id"] == order["order_id"] and r.json()["path"] == "signed webhook"
    paid = merchant.get(f"/orders/{order['order_id']}").json()
    assert paid["status"] == "paid" and paid["paid_via"].startswith("cybersource_webhook")
    assert client.post("/host/api/confirm-payment").status_code == 409  # nothing left to pay


def test_confirm_payment_without_a_webhook_key_uses_the_callback(client, merchant, monkeypatch):
    monkeypatch.delenv("CYBS_WEBHOOK_KEY_ID", raising=False)
    monkeypatch.delenv("CYBS_WEBHOOK_KEY", raising=False)
    order = place(merchant)
    assert client.post("/host/api/confirm-payment").json()["path"] == "callback"
    assert merchant.get(f"/orders/{order['order_id']}").json()["paid_via"] == "host_confirmed"


def test_status_shows_the_order_and_approval(client, merchant):
    order = place(merchant)
    client.post("/events", json={"type": "approval_requested", "session_id": "s1", "mandate_id": "m",
                                 "t": int(time.time() * 1000), "source": "policy", "approval_id": "a_1",
                                 "amount": 49.95, "expires_at": "2026-09-26T12:00:00+00:00"})
    s = client.get("/host/api/status").json()
    assert s["order"]["order_id"] == order["order_id"] and s["order"]["status"] == "awaiting_payment"
    assert s["approval"]["approval_id"] == "a_1"


def test_arm_replay_posts_an_event(client):
    assert client.post("/host/api/arm-replay").status_code == 200
    assert ledger.LEDGER.read_live()[-1]["type"] == "replay_armed"


def test_approval_code_needs_an_approval(client):
    assert client.get("/host/api/approval-code").status_code == 404


@pytest.mark.parametrize("header", ["x-forwarded-for", "ngrok-trace-id", "forwarded"])
def test_proxied_requests_are_refused(client, header):
    assert client.get("/host", headers={header: "1.2.3.4"}).status_code == 403
    assert client.post("/host/api/reset", headers={header: "1.2.3.4"}).status_code == 403


def test_public_addresses_are_refused():
    class Req:
        headers: dict = {}
        client = type("C", (), {"host": "8.8.8.8"})()
    with pytest.raises(Exception) as err:
        host.lan_only(Req())
    assert err.value.status_code == 403
    Req.client.host = "192.168.8.11"
    host.lan_only(Req())


def test_host_page_is_served(client):
    r = client.get("/host")
    assert r.status_code == 200 and "Confirm payment" in r.text


def test_host_reset_runs_the_real_reset(client):
    r = client.post("/host/api/reset")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["merchant"] == "ok" and isinstance(body["ms"], int)
