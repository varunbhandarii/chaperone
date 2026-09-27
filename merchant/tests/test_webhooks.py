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
from common.host_header import HEADERS as HOST  # noqa: E402

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
    with TestClient(app, headers=HOST) as c:
        c.post("/reset")
        yield c


def notify(client, body: str, headers: dict):
    return client.post("/webhooks/cybersource", content=body.encode(), headers=headers)


def new_order(client):
    return client.post("/orders", json={**DEMO, "decision_id": "d_" + secrets.token_hex(6)}).json()


def test_signed_payment_marks_the_order_paid(client):
    order = new_order(client)
    body = json.dumps(envelope(order))
    r = notify(client, body, webhooks.headers_for(body, KEY_ID, KEY))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid" and r.json()["order_id"] == order["order_id"]
    assert client.get(f"/orders/{order['order_id']}").json()["paid_via"] == "cybersource_webhook (raw_body)"
    assert client.get("/panel").json()["events"][-1]["type"] == "paid"


def test_plugin_message_is_timestamp_dot_compact_payload():
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


def test_payload_variant_is_also_accepted(client):
    order = new_order(client)
    body = json.dumps(envelope(order))
    r = notify(client, body, webhooks.headers_for(body, KEY_ID, KEY, variant="payload"))
    assert r.status_code == 200 and r.json()["status"] == "paid"
    assert client.get(f"/orders/{order['order_id']}").json()["paid_via"] == "cybersource_webhook (payload)"


def test_tampered_envelope_cannot_pay_another_order(client):
    """A notification signed (payload variant) for order A, with order B's ids added to the unsigned envelope."""
    a, b = new_order(client), new_order(client)
    env = envelope(a)
    env["extra"] = {"purchaseNumber": b["payment_link"]["purchase_number"], "id": b["payment_link"]["id"]}
    env["eventType"] = "payByLink.merchant.payment"
    body = json.dumps(env)
    signed_for_a = webhooks.headers_for(json.dumps(envelope(a) | {"payload": env["payload"]}), KEY_ID, KEY,
                                        variant="payload")
    r = notify(client, body, signed_for_a)
    assert r.status_code == 200 and r.json()["order_id"] == a["order_id"]
    assert client.get(f"/orders/{b['order_id']}").json()["status"] == "awaiting_payment"


def test_unsigned_event_type_is_not_trusted(client):
    """Payload variant: a payload with no payment in it stays unpaid whatever the envelope's eventType says."""
    order = new_order(client)
    env = envelope(order)
    env["payload"][0]["data"]["status"] = "CANCELLED"
    body = json.dumps(env)
    r = notify(client, body, webhooks.headers_for(body, KEY_ID, KEY, variant="payload"))
    assert r.status_code == 200 and r.json()["matched"] is True and "ignored" in r.json()
    assert client.get(f"/orders/{order['order_id']}").json()["status"] == "awaiting_payment"


def test_non_ascii_key_id_is_401_not_500(client):
    body = json.dumps(envelope(new_order(client)))
    headers = webhooks.headers_for(body, KEY_ID, KEY)
    headers["v-c-signature"] = headers["v-c-signature"].replace(KEY_ID, "clé-ñ")
    r = notify(client, body, {k: v.encode("utf-8") for k, v in headers.items()})
    assert r.status_code == 401 and r.json()["detail"] == "unknown keyId"


def test_replay_of_a_paid_notification_does_not_pay_again(client):
    order = new_order(client)
    body = json.dumps(envelope(order))
    notify(client, body, webhooks.headers_for(body, KEY_ID, KEY))
    fresh = webhooks.headers_for(body, KEY_ID, KEY, t=int(time.time() * 1000) + 1)  # new signature, same order
    r = notify(client, body, fresh)
    assert r.status_code == 200 and r.json()["duplicate"] is True
    assert [e["type"] for e in client.get("/panel").json()["events"]].count("paid") == 1


def test_missing_or_malformed_header(client):
    body = json.dumps(envelope(new_order(client)))
    assert notify(client, body, {"Content-Type": "application/json"}).status_code == 401
    assert notify(client, body, {"v-c-signature": "garbage"}).status_code == 401


def test_deeply_nested_body_is_401_not_500(client):
    header = f"t={int(time.time() * 1000)};keyId={KEY_ID};sig=AAAA"
    assert notify(client, "[" * 200_000, {"v-c-signature": header}).status_code == 401


def test_unconfigured_key_is_503(client, monkeypatch):
    monkeypatch.delenv("CYBS_WEBHOOK_KEY")
    body = json.dumps(envelope(new_order(client)))
    assert notify(client, body, {"v-c-signature": "t=1;keyId=x;sig=y"}).status_code == 503


def test_health_answers_get_and_post(client):
    assert client.get("/webhooks/cybersource/health").status_code == 200
    assert client.post("/webhooks/cybersource/health").status_code == 200


def test_each_stores_own_key_pays_only_that_stores_orders(client, monkeypatch):
    park_id, park_key = "park-key-1", base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("CYBS_PARKSIDE_WEBHOOK_KEY_ID", park_id)
    monkeypatch.setenv("CYBS_PARKSIDE_WEBHOOK_KEY", park_key)
    corner = new_order(client)
    parkside = client.post("/orders", json={**DEMO, "decision_id": "d_" + secrets.token_hex(6),
                                            "cart": {"merchant": "parkside_pharmacy",
                                                     "items": [{"sku": "RX-001", "qty": 1}]}}).json()
    body = json.dumps(envelope(corner))
    r = notify(client, body, webhooks.headers_for(body, park_id, park_key))
    assert r.status_code == 200 and r.json()["matched"] is False  # Parkside's key can't pay Corner Market
    assert client.get(f"/orders/{corner['order_id']}").json()["status"] == "awaiting_payment"
    body = json.dumps(envelope(parkside))
    r = notify(client, body, webhooks.headers_for(body, park_id, park_key))
    assert r.json()["status"] == "paid"
    body = json.dumps(envelope(corner))  # the shared key still pays any store
    assert notify(client, body, webhooks.headers_for(body, KEY_ID, KEY)).json()["status"] == "paid"


def test_the_main_accounts_key_pays_a_store_that_falls_back_to_it(client, monkeypatch):
    main_id, main_key = "main-key-1", base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("VISA_ACCEPTANCE_WEBHOOK_KEY_ID", main_id)
    monkeypatch.setenv("VISA_ACCEPTANCE_WEBHOOK_KEY", main_key)
    for var in ("MERCHANT_ID", "API_KEY_ID", "SECRET_KEY"):  # Parkside has no account of its own here
        monkeypatch.delenv(f"CYBS_PARKSIDE_{var}", raising=False)
    parkside = client.post("/orders", json={**DEMO, "decision_id": "d_" + secrets.token_hex(6),
                                            "cart": {"merchant": "parkside_pharmacy",
                                                     "items": [{"sku": "RX-001", "qty": 1}]}}).json()
    body = json.dumps(envelope(parkside))
    assert notify(client, body, webhooks.headers_for(body, main_id, main_key)).json()["status"] == "paid"
