import json
from pathlib import Path

from fastapi.testclient import TestClient

from policy.main import app

DEMO = json.loads((Path(__file__).parent / "demo_cart.json").read_text(encoding="utf-8"))


def client(tmp_path, monkeypatch, *, unsigned="1"):
    monkeypatch.setenv("DECISIONS_PATH", str(tmp_path / "decisions.json"))
    monkeypatch.setenv("MONTHLY_PATH", str(tmp_path / "monthly.json"))
    monkeypatch.setenv("MANDATE_PATH", str(tmp_path / "mandate.json"))
    monkeypatch.setenv("JUDGE_FAKE", "1")
    monkeypatch.setenv("MANDATE_UNSIGNED_OK", unsigned)
    monkeypatch.setenv("RELAY_URL", "")
    return TestClient(app)


def test_demo_cart_allows_and_returns_the_merchant_link(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "policy.checkout.send_signed_order",
        lambda body: {"order_id": "ord_test", "status": "awaiting_payment", "payment_link": {"url": "http://127.0.0.1:8002/pay/abc"}},
    )
    response = client(tmp_path, monkeypatch).post("/checkout", json=DEMO)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "allow"
    assert body["order"]["payment_link"] == "http://127.0.0.1:8002/pay/abc"
    assert body["detail"] == "unsigned mandate"


def test_unsigned_mandate_is_refused_without_the_flag(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch, unsigned="0").post("/checkout", json=DEMO)
    assert response.status_code == 403


def test_unknown_sku_is_rejected(tmp_path, monkeypatch):
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"][0]["sku"] = "NO-SUCH"
    response = client(tmp_path, monkeypatch).post("/checkout", json=payload)
    assert response.status_code == 422


def test_budget_starts_at_the_baseline(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch).get("/budget", params={"mandate_id": "m_ruth_2026_09"})
    assert response.json() == {"monthly_cap": 300.0, "spent": 142.1, "left": 157.9}
