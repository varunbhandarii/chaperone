import asyncio
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


ALLOWED = {"decision_id": "dec-001", "decision": "allow", "cart": {"items": [{"sku": "RX-001", "qty": 1}]}}


def run_verify(*args, **kwargs):
    return asyncio.run(verify.verify_request(*args, **kwargs))


def policy_says(decision):
    return lambda decision_id: decision if decision and decision_id == decision.get("decision_id") else None


def check(prepared, store, body=None, decision=ALLOWED):
    return run_verify(*parts(prepared, body), public_key=KEY.public_key(), nonce_store=store,
                                 fetch_decision=policy_says(decision))


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
    monkeypatch.setattr(verify, "fetch_decision", policy_says(ALLOWED))
    prepared = sign_request(URL, ORDER, private_key=KEY)
    response = TestClient(app).post("/orders", content=prepared.body, headers=dict(prepared.headers))
    assert response.status_code == 200, response.text
    assert all(c["passed"] for c in response.json()["verification"])


def test_off_mode_skips(monkeypatch):
    monkeypatch.setenv("MERCHANT_VERIFY", "off")
    result = run_verify("POST", "testserver", "/orders", {}, b"{}")
    assert result.ok
    assert result.checks[0]["passed"] is None


def test_unknown_decision_is_rejected():
    result = check(sign_request(URL, ORDER, private_key=KEY), verify.NonceStore(), decision=None)
    assert not result.ok
    assert result.checks[-1] == {"id": "decision", "passed": False, "detail": "unknown decision dec-001"}
    assert all(c["passed"] for c in result.checks[:-1])  # the signature itself was fine


def test_cart_different_from_the_decision_is_rejected():
    decided = {**ALLOWED, "cart": {"items": [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 1}]}}
    result = check(sign_request(URL, ORDER, private_key=KEY), verify.NonceStore(), decision=decided)
    assert not result.ok and result.checks[-1]["id"] == "decision"
    assert "cart differs" in result.checks[-1]["detail"]


def test_denied_decision_is_rejected():
    result = check(sign_request(URL, ORDER, private_key=KEY), verify.NonceStore(), decision={**ALLOWED, "decision": "deny"})
    assert not result.ok and result.checks[-1]["detail"] == "decision dec-001 is deny"


def test_approve_needs_the_caregiver_yes():
    waiting = {**ALLOWED, "decision": "approve", "approval": {"approved": False}}
    assert not check(sign_request(URL, ORDER, private_key=KEY), verify.NonceStore(), decision=waiting).ok
    approved = {**ALLOWED, "decision": "approve", "approval": {"approved": True}}
    assert check(sign_request(URL, ORDER, private_key=KEY), verify.NonceStore(), decision=approved).ok


def test_cart_lines_are_compared_by_sku_and_total_qty():
    split = {**ALLOWED, "cart": {"lines": [{"sku": "RX-001", "qty": 1}]}}
    assert check(sign_request(URL, ORDER, private_key=KEY), verify.NonceStore(), decision=split).ok


def test_policy_down_fails_closed(monkeypatch):
    monkeypatch.setenv("POLICY_URL", "http://127.0.0.1:9")  # nothing listens there
    result = run_verify(*parts(sign_request(URL, ORDER, private_key=KEY)), public_key=KEY.public_key(),
                                   nonce_store=verify.NonceStore())
    assert not result.ok
    assert result.checks[-1]["id"] == "decision" and "policy unreachable" in result.checks[-1]["detail"]


def test_policy_5xx_is_a_decision_failure_not_a_signature_failure(monkeypatch):
    import httpx

    def boom(request):
        return httpx.Response(503, text="policy restarting")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(verify.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(boom), **kw))
    result = run_verify(*parts(sign_request(URL, ORDER, private_key=KEY)), public_key=KEY.public_key(),
                        nonce_store=verify.NonceStore())
    assert not result.ok
    assert all(c["passed"] for c in result.checks[:-1])
    assert result.checks[-1] == {"id": "decision", "passed": False, "detail": "policy answered 503"}
