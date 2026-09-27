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
from common.host_header import HEADERS as HOST  # noqa: E402

ORDER = {"mandate_id": "m_ruth_2026_09", "session_id": "s1",
         "cart": {"items": [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 1}]}}


@pytest.fixture
def merchant():
    with TestClient(merchant_app, headers=HOST) as m:
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


def test_confirm_payment_pays_every_store_of_one_decision(client, merchant, monkeypatch):
    """Medicine at the pharmacy and bread at the grocery are two orders under one decision: one press pays both."""
    monkeypatch.delenv("CYBS_WEBHOOK_KEY_ID", raising=False)
    monkeypatch.delenv("CYBS_WEBHOOK_KEY", raising=False)
    other = place(merchant)  # an earlier, different decision stays unpaid
    time.sleep(0.01)
    decision = "d_" + secrets.token_hex(6)
    base = {"mandate_id": "m_ruth_2026_09", "session_id": "s1", "decision_id": decision}
    rx = merchant.post("/orders", json={**base, "cart": {"merchant": "parkside_pharmacy", "items": [{"sku": "RX-001", "qty": 1}]}}).json()
    bread = merchant.post("/orders", json={**base, "cart": {"merchant": "corner_market", "items": [{"sku": "BAK-001", "qty": 1}]}}).json()
    r = client.post("/host/api/confirm-payment")
    assert r.status_code == 200, r.text
    assert sorted(r.json()["order_ids"]) == sorted([rx["order_id"], bread["order_id"]])
    assert {merchant.get(f"/orders/{o}").json()["status"] for o in r.json()["order_ids"]} == {"paid"}
    assert merchant.get(f"/orders/{other['order_id']}").json()["status"] == "awaiting_payment"


def test_confirm_payment_without_a_webhook_key_uses_the_callback(client, merchant, monkeypatch):
    monkeypatch.delenv("CYBS_WEBHOOK_KEY_ID", raising=False)
    monkeypatch.delenv("CYBS_WEBHOOK_KEY", raising=False)
    order = place(merchant)
    assert client.post("/host/api/confirm-payment").json()["path"] == "callback"
    assert merchant.get(f"/orders/{order['order_id']}").json()["paid_via"] == "host_confirmed"


def test_picked_up_closes_the_latest_paid_order(client, merchant, monkeypatch):
    monkeypatch.delenv("CYBS_WEBHOOK_KEY_ID", raising=False)
    monkeypatch.delenv("CYBS_WEBHOOK_KEY", raising=False)
    order = place(merchant)
    assert client.post("/host/api/picked-up").status_code == 409  # not paid yet
    client.post("/host/api/confirm-payment")
    r = client.post("/host/api/picked-up")
    assert r.status_code == 200, r.text
    assert r.json()["order_id"] == order["order_id"] and r.json()["pickup_code"] == order["pickup_code"]
    assert merchant.get(f"/orders/{order['order_id']}").json()["fulfilment"] == "picked_up"
    assert client.get("/host/api/status").json()["order"]["fulfilment"] == "picked_up"
    assert client.post("/host/api/picked-up").status_code == 409  # nothing left to pick up
    page = client.get("/host").text
    assert 'id="pickup"' in page and 'key === "u"' in page


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


@pytest.mark.parametrize("path", ["/host/api/confirm-payment", "/host/api/picked-up", "/host/api/reset",
                                  "/host/api/arm-replay", "/reset"])
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


def test_session_page_and_dispute_record_show_the_order_after_payment(client, merchant, monkeypatch):
    monkeypatch.setenv("REFUND_TRANSMIT_S", "0")
    order = place(merchant)
    merchant.post(f"/orders/{order['order_id']}/paid", params={"via": "host_confirmed"})
    refund = {"mandate_id": "m_ruth_2026_09", "decision_id": order["decision_id"], "order_id": order["order_id"],
              "sku": "BAK-001", "qty": 1, "reason": "stale"}
    assert merchant.post(f"/orders/{order['order_id']}/refunds", json=refund).status_code == 200
    for e in ({"type": "heard", "role": "shopper", "text": "medicine and bread", "item_id": "t1", "source": "station"},
              {"type": "policy_decision", "decision": "allow", "decision_id": order["decision_id"], "source": "policy"},
              {"type": "paid", "order_id": order["order_id"], "total": "11.49", "via": "host", "source": "merchant"},
              {"type": "refund_result", "order_id": order["order_id"], "refund_id": "r1", "status": "PENDING",
               "amount": "3.49", "card_last4": "1111", "source": "merchant"}):
        client.post("/events", json={"session_id": "s1", "mandate_id": "m_ruth_2026_09", "t": int(time.time() * 1000), **e})
    page = client.get("/sessions/s1", params={"format": "html"}).text
    assert "Dispute-ready record" in page and 'href="s1/record.json"' in page
    assert "partly refunded" in page and "Refund $3.49" in page and "sandbox processor stub" in page
    timeline = page.split(f'{order["order_id"]}</span></h2>')[1][:400]  # the order's own section
    assert order["pickup_code"] not in timeline and "Corner Market ·" in page
    assert '<meta http-equiv="refresh" content="30">' in page and "Corner Market · Visa sandbox" not in page
    r = client.get("/sessions/s1/record.json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    record = r.json()
    assert record["kind"] == "chaperone.dispute_record.v1"
    assert record["shopper_words"][0]["text"] == "medicine and bread"
    assert record["decisions"][0]["decision_id"] == order["decision_id"]
    [stored] = record["orders"]
    assert stored["status"] == "partially_refunded" and stored["refunds"][0]["amount"] == "3.49"
    assert "pickup_code" not in stored and "url" not in stored["payment_link"]
    assert record["mandate"] is None  # policy is down in this fixture; the record still builds
    assert client.get("/sessions/nobody/record.json").status_code == 404
    assert client.get("/sessions/none/record.json").status_code == 404


def test_record_carries_the_mandate_hash_the_passkey_signed():
    from policy.mandate import DEFAULT_MANDATE, mandate_hash
    from relay import session_view

    digest = base64.urlsafe_b64encode(mandate_hash(DEFAULT_MANDATE)).rstrip(b"=").decode()
    mandate = {"mandate_id": DEFAULT_MANDATE["mandate_id"], "signed": True, "credential_id": "cred-1", "hash_b64url": digest}
    record = session_view.dispute_record("s1", [], [], mandate)
    page = session_view.render("s1", [], [], [], record)
    assert record["mandate"]["hash_b64url"] == digest and digest in page and "signed by passkey cred-1" in page


def test_session_page_and_record_carry_card_swipes_and_scam_checks(client, merchant):
    now = int(time.time() * 1000)
    base = {"mandate_id": "m_ruth_2026_09", "t": now}
    for e in ({"type": "heard", "session_id": "s9", "role": "shopper", "text": "they said my power is cut", "source": "station"},
              {"type": "scam_checked", "session_id": "s9", "check_id": "sc9", "verdict": "scam", "pattern": "utility",
               "sources": [{"title": "FTC", "url": "https://ftc.gov"}], "source": "policy"},
              {"type": "card_decision", "session_id": "none", "token": "tok9", "store": "Five Points Drug", "mcc": "5912",
               "amount": 480, "result": "declined", "reason_key": "card_cooldown", "reason": "cool-down", "source": "policy"},
              {"type": "vtc_decision", "session_id": "none", "token": "tok9", "store": "Five Points Drug", "mcc": "5912",
               "amount": 480, "should_decline": True, "rule": "PCT_GLOBAL", "source": "relay"}):
        assert client.post("/events", json={**base, **e}).status_code == 202
    page = client.get("/sessions/s9", params={"format": "html"}).text
    assert "Scam check" in page and "Declined $480.00" in page and "Visa VTC: decline" in page
    record = client.get("/sessions/s9/record.json").json()
    assert record["scam_checks"][0]["verdict"] == "scam"
    assert [c["type"] for c in record["card_decisions"]] == ["card_decision", "vtc_decision"]
