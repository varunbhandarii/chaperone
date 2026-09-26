import datetime
import json
import os
from urllib.parse import urlsplit

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from merchant import verify  # noqa: E402
from merchant.orders import app  # noqa: E402
from signer.sign import sign_request  # noqa: E402

KEY = Ed25519PrivateKey.generate()
URL = "http://testserver/orders"
ORDER = {
    "mandate_id": "mandate-ruth-001",
    "decision_id": "dec-001",
    "session_id": "s1",
    "cart": {"items": [{"sku": "RX-001", "qty": 1}]},
}


def parts(prepared, body=None):
    split = urlsplit(prepared.url)
    headers = {k.lower(): v for k, v in prepared.headers.items()}
    raw = prepared.body if body is None else body
    return prepared.method, split.netloc, split.path, headers, raw


def check(prepared, store, body=None):
    return verify.verify_request(*parts(prepared, body), public_key=KEY.public_key(), nonce_store=store)


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("MERCHANT_VERIFY", "enforce")


def test_valid_signature_passes_every_check():
    result = check(sign_request(URL, ORDER, private_key=KEY), verify.NonceStore())
    assert result.ok
    assert result.keyid == "chaperone-agent-1"
    assert [c["id"] for c in result.checks] == list(verify.STEPS)
    assert all(c["passed"] for c in result.checks)


def test_changed_body_fails_the_digest():
    prepared = sign_request(URL, ORDER, private_key=KEY)
    tampered = json.dumps({**ORDER, "cart": {"items": [{"sku": "RX-001", "qty": 9}]}}).encode()
    result = check(prepared, verify.NonceStore(), body=tampered)
    assert not result.ok
    assert result.checks == [{"id": "content_digest", "passed": False, "detail": "content digest mismatch"}]


def test_replayed_nonce_is_rejected():
    store = verify.NonceStore()
    prepared = sign_request(URL, ORDER, private_key=KEY)
    assert check(prepared, store).ok
    again = check(prepared, store)
    assert not again.ok
    assert again.checks[-1] == {"id": "nonce", "passed": False, "detail": "rejected: replay"}


def test_expired_signature_is_rejected():
    created = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)
    prepared = sign_request(URL, ORDER, private_key=KEY, created=created)
    result = check(prepared, verify.NonceStore())
    assert not result.ok
    assert result.checks[-1]["id"] == "signature"


def test_window_longer_than_eight_minutes_is_rejected():
    now = datetime.datetime.now(datetime.timezone.utc)
    prepared = sign_request(URL, ORDER, private_key=KEY, created=now, expires=now + datetime.timedelta(minutes=30))
    result = check(prepared, verify.NonceStore())
    assert not result.ok
    assert result.checks[-1]["id"] == "window"


def test_wrong_key_is_rejected():
    prepared = sign_request(URL, ORDER, private_key=Ed25519PrivateKey.generate())
    result = check(prepared, verify.NonceStore())
    assert not result.ok
    assert result.checks[-1]["id"] == "signature"


def test_nonce_outlives_the_library_clock_skew():
    store = verify.NonceStore()
    expires = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=2)
    store.consume("n1", expires)
    with pytest.raises(verify.ReplayError):
        store.consume("n1", expires)


def test_unsigned_order_is_refused_when_enforcing():
    response = TestClient(app).post("/orders", json=ORDER)
    assert response.status_code == 401


def test_signed_order_goes_through_the_endpoint(monkeypatch):
    monkeypatch.setattr(verify, "jwks_lookup", lambda key_id: KEY.public_key())
    prepared = sign_request(URL, ORDER, private_key=KEY)
    response = TestClient(app).post("/orders", content=prepared.body, headers=dict(prepared.headers))
    assert response.status_code == 200, response.text
    assert all(c["passed"] for c in response.json()["verification"])


def test_off_mode_skips(monkeypatch):
    monkeypatch.setenv("MERCHANT_VERIFY", "off")
    result = verify.verify_request("POST", "testserver", "/orders", {}, b"{}")
    assert result.ok
    assert result.checks[0]["passed"] is None
