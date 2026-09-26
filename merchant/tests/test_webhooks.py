import base64
import json
import os
import secrets
import time

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from merchant import webhooks  # noqa: E402
from merchant.orders import app  # noqa: E402
from merchant.simulate_payment import envelope  # noqa: E402

KEY_ID = "8a2b7c4d-0000-4000-8000-000000000001"
KEY = base64.b64encode(secrets.token_bytes(32)).decode()
DEMO = {"mandate_id": "m_ruth_2026_09", "decision_id": "d_000000000001", "session_id": "s1",
        "cart": {"items": [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 1}]}}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("CYBS_WEBHOOK_KEY_ID", KEY_ID)
    monkeypatch.setenv("CYBS_WEBHOOK_KEY", KEY)
    webhooks._seen_signatures.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/reset")
        yield c


def notify(client, body: str, headers: dict):
    return client.post("/webhooks/cybersource", content=body.encode(), headers=headers)


def new_order(client):
    return client.post("/orders", json=DEMO).json()


def test_signed_payment_marks_the_order_paid(client):
    order = new_order(client)
    body = json.dumps(envelope(order))
    r = notify(client, body, webhooks.headers_for(body, KEY_ID, KEY))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid" and r.json()["order_id"] == order["order_id"]
    assert client.get(f"/orders/{order['order_id']}").json()["paid_via"] == "cybersource_webhook (payload)"
    assert client.get("/panel").json()["events"][-1]["type"] == "paid"


def test_official_message_is_timestamp_dot_compact_payload():
    body = {"payload": [{"data": {"x": "ñ", "n": 1}}], "eventType": "e"}
    t = 1790000000000
    expected = base64.b64encode(__import__("hmac").new(
        base64.b64decode(KEY), f'{t}.[{{"data":{{"x":"ñ","n":1}}}}]'.encode(), "sha256").digest()).decode()
    assert webhooks.sign(KEY, t, webhooks.compact(body["payload"])) == expected


def test_changed_byte_fails(client):
    order = new_order(client)
    body = json.dumps(envelope(order))
    headers = webhooks.headers_for(body, KEY_ID, KEY)
    tampered = body.replace(order["amount"], "0.01")
    assert notify(client, tampered, headers).status_code == 401
    assert client.get(f"/orders/{order['order_id']}").json()["status"] == "awaiting_payment"


def test_wrong_key_id_fails(client):
    body = json.dumps(envelope(new_order(client)))
    r = notify(client, body, webhooks.headers_for(body, "someone-else", KEY))
    assert r.status_code == 401 and r.json()["detail"] == "unknown keyId"


def test_wrong_key_fails(client):
    body = json.dumps(envelope(new_order(client)))
    other = base64.b64encode(secrets.token_bytes(32)).decode()
    assert notify(client, body, webhooks.headers_for(body, KEY_ID, other)).status_code == 401


def test_stale_replay_fails(client):
    body = json.dumps(envelope(new_order(client)))
    two_hours_ago = int(time.time() * 1000) - 2 * 60 * 60 * 1000
    r = notify(client, body, webhooks.headers_for(body, KEY_ID, KEY, t=two_hours_ago))
    assert r.status_code == 401 and r.json()["detail"] == "stale timestamp"


def test_repeat_delivery_is_idempotent(client):
    order = new_order(client)
    body = json.dumps(envelope(order))
    headers = webhooks.headers_for(body, KEY_ID, KEY)
    first, second = notify(client, body, headers).json(), notify(client, body, headers).json()
    assert first["duplicate"] is False and second["duplicate"] is True
    assert [e["type"] for e in client.get("/panel").json()["events"]].count("paid") == 1


def test_unknown_purchase_number_is_acknowledged_not_paid(client):
    order = new_order(client)
    env = envelope(order)
    env["payload"][0]["data"]["purchaseInformation"]["purchaseNumber"] = "NOTOURS123"
    env["payload"][0]["data"]["id"] = "not-ours"
    body = json.dumps(env)
    r = notify(client, body, webhooks.headers_for(body, KEY_ID, KEY))
    assert r.status_code == 200 and r.json()["matched"] is False


def test_raw_body_variant_is_also_accepted(client):
    order = new_order(client)
    body = json.dumps(envelope(order))
    t = int(time.time() * 1000)
    headers = {"Content-Type": "application/json",
               "v-c-signature": f"t={t};keyId={KEY_ID};sig={webhooks.sign(KEY, t, body)}"}
    r = notify(client, body, headers)
    assert r.status_code == 200 and r.json()["status"] == "paid"


def test_missing_or_malformed_header(client):
    body = json.dumps(envelope(new_order(client)))
    assert notify(client, body, {"Content-Type": "application/json"}).status_code == 401
    assert notify(client, body, {"v-c-signature": "garbage"}).status_code == 401


def test_unconfigured_key_is_503(client, monkeypatch):
    monkeypatch.delenv("CYBS_WEBHOOK_KEY")
    body = json.dumps(envelope(new_order(client)))
    assert notify(client, body, {"v-c-signature": "t=1;keyId=x;sig=y"}).status_code == 503


def test_health_answers_get_and_post(client):
    assert client.get("/webhooks/cybersource/health").status_code == 200
    assert client.post("/webhooks/cybersource/health").status_code == 200
