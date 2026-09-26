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

    class Routes(httpx.AsyncBaseTransport):
        """http://merchant goes to the merchant app in-process; anything else (policy) is down."""

        def __init__(self):
            self.merchant = httpx.ASGITransport(app=merchant_app)

        async def handle_async_request(self, request):
            if request.url.host != "merchant":
                raise httpx.ConnectError("nothing listens there", request=request)
            return await self.merchant.handle_async_request(request)

    def routed(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=Routes(), **kwargs)
    monkeypatch.setattr(host.httpx, "AsyncClient", routed)
    return TestClient(ledger.app, headers={"X-Chaperone-Host": "1"})  # the Host page's header


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


@pytest.mark.parametrize("path", ["/host/api/confirm-payment", "/host/api/reset", "/host/api/arm-replay", "/reset"])
def test_actions_need_the_host_header(client, path):
    """A page open in another LAN browser can POST a form, but cannot add a custom header."""
    bare = TestClient(ledger.app)
    assert bare.post(path).status_code == 403
    assert bare.post(path, headers={"X-Chaperone-Host": "0"}).status_code == 403
    assert client.post(path).status_code in (200, 409)  # 409: no order waiting to be paid


def test_reads_do_not_need_the_header(client):
    bare = TestClient(ledger.app)
    assert bare.get("/host").status_code == 200 and bare.get("/host/api/status").status_code == 200


def test_partial_reset_is_reported_and_the_ledger_is_still_cleared(client):
    client.post("/events", json={"type": "heard", "session_id": "s1", "mandate_id": "m", "t": 1, "source": "station"})
    r = client.post("/host/api/reset").json()  # merchant answers in-process; policy is unreachable
    assert r["ok"] is False and r["failed"] == ["policy"] and r["merchant"] == "ok"
    live = ledger.LEDGER.read_live()
    assert [e["type"] for e in live] == ["reset"] and live[0]["failed"] == ["policy"]


def test_host_page_sends_the_header_and_reports_failures_in_red():
    page = (host.HOST_HTML).read_text()
    assert '"X-Chaperone-Host": "1"' in page and "NOT CLEAN" in page
