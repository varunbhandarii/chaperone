import json

import httpx
import pytest
from fastapi.testclient import TestClient

from relay import host, ledger

HOST = {"X-Chaperone-Host": "1"}


@pytest.fixture
def policy(monkeypatch, tmp_path):
    """Policy's /card/simulate, answered in-process; records what the relay sent."""
    monkeypatch.setattr(ledger, "LEDGER", ledger.Ledger(tmp_path / "live.jsonl"))
    monkeypatch.setenv("POLICY_URL", "http://policy")
    seen, answer = [], {"status": 200, "body": {"token": "tok_1", "result": "SUSPECTED_FRAUD", "status": "DECIDED",
                                                "reason_key": "card_cooldown", "ms": 212,
                                                "reason": "Ruth's card is on a cool-down after a scam check.",
                                                "store": "Five Points Drug", "card_last4": "4242"}}

    def handler(request: httpx.Request):
        seen.append({"path": request.url.path, "host": request.headers.get("x-chaperone-host"),
                     "body": json.loads(request.content)})
        return httpx.Response(answer["status"], json=answer["body"])

    real = httpx.AsyncClient
    monkeypatch.setattr(host.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))
    return seen, answer


def test_terminal_page_lists_the_registry_stores():
    page = TestClient(ledger.app).get("/terminal")
    assert page.status_code == 200
    for name in ("Five Points Drug", "Corner Market", "GiftCard Kiosk", "Coin ATM"):
        assert name in page.text
    assert "__STORES__" not in page.text and '"X-Chaperone-Host": "1"' in page.text


def test_terminal_is_lan_only():
    assert TestClient(ledger.app).get("/terminal", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 403


def test_swipe_asks_policy_with_the_host_header(policy):
    seen, _ = policy
    r = TestClient(ledger.app).post("/host/api/swipe", headers=HOST,
                                    json={"acceptor_id": "FIVEPTSDRUG01", "amount_cents": 48000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "SUSPECTED_FRAUD" and body["reason_key"] == "card_cooldown" and body["mcc"] == "5912"
    assert seen == [{"path": "/card/simulate", "host": "1", "body": {"acceptor_id": "FIVEPTSDRUG01", "amount_cents": 48000}}]


@pytest.mark.parametrize("payload,status", [
    ({"acceptor_id": "NOPE", "amount_cents": 100}, 404),
    ({"acceptor_id": "FIVEPTSDRUG01", "amount_cents": 0}, 422),
    ({"acceptor_id": "FIVEPTSDRUG01"}, 422),
])
def test_bad_swipes_never_reach_policy(policy, payload, status):
    seen, _ = policy
    assert TestClient(ledger.app).post("/host/api/swipe", headers=HOST, json=payload).status_code == status
    assert seen == []


def test_swipe_needs_the_host_header(policy):
    r = TestClient(ledger.app).post("/host/api/swipe", json={"acceptor_id": "FIVEPTSDRUG01", "amount_cents": 100})
    assert r.status_code == 403


def test_card_not_enrolled_is_passed_on(policy):
    _, answer = policy
    answer.update(status=503, body={"detail": "Ruth's card is not enrolled yet"})
    r = TestClient(ledger.app).post("/host/api/swipe", headers=HOST,
                                    json={"acceptor_id": "GIFTCARDMALL1", "amount_cents": 5000})
    assert r.status_code == 503 and r.json()["detail"] == "Ruth's card is not enrolled yet"
