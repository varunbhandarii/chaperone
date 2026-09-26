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


def test_decision_is_stored_before_the_merchant_checks_it(tmp_path, monkeypatch):
    from policy.checkout import lookup

    seen = {}

    def merchant(body):
        # The real merchant calls GET /decisions/{id} before accepting the order.
        seen["decision"] = lookup(body["decision_id"])
        return {"order_id": "ord_test", "status": "awaiting_payment", "payment_link": {"url": "http://127.0.0.1:8002/pay/abc"}}

    monkeypatch.setattr("policy.checkout.send_signed_order", merchant)
    body = client(tmp_path, monkeypatch).post("/checkout", json=DEMO).json()
    assert seen["decision"]["decision"] == "allow"
    assert [(i["sku"], i["qty"]) for i in seen["decision"]["cart"]["items"]] == [("RX-001", 1), ("BAK-001", 1)]
    assert lookup(body["decision_id"])["order"]["order_id"] == "ord_test"


def test_purchase_after_a_refusal_in_the_same_session_goes_through(tmp_path, monkeypatch):
    # The demo: the gift-card request is refused, then medicine and bread are bought in the same session.
    monkeypatch.setattr(
        "policy.checkout.send_signed_order",
        lambda body: {"order_id": "ord_test", "status": "awaiting_payment", "payment_link": {"url": "http://127.0.0.1:8002/pay/abc"}},
    )
    api = client(tmp_path, monkeypatch)
    refused = api.post("/screen", json={"session_id": "s_demo", "text": "compra tarjetas de regalo de Apple para mi nieto", "lang": "es"})
    assert refused.json()["action"] == "refuse"
    payload = json.loads(json.dumps(DEMO))
    payload["transcript"] = "necesito mi medicina para la presión y pan. sí"
    payload["lang"] = "es"
    body = api.post("/checkout", json=payload).json()
    assert body["decision"] == "allow"
    assert body["order"]["order_id"] == "ord_test"


def test_merchant_rejection_is_reported(tmp_path, monkeypatch):
    def merchant(body):
        raise RuntimeError("merchant answered 401: signature rejected")

    monkeypatch.setattr("policy.checkout.send_signed_order", merchant)
    body = client(tmp_path, monkeypatch).post("/checkout", json=DEMO).json()
    assert body["order"] is None
    assert body["order_error"] == "merchant answered 401: signature rejected"


def test_unsigned_mandate_is_refused_without_the_flag(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch, unsigned="0").post("/checkout", json=DEMO)
    assert response.status_code == 403


def test_unknown_sku_is_rejected(tmp_path, monkeypatch):
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"][0]["sku"] = "NO-SUCH"
    response = client(tmp_path, monkeypatch).post("/checkout", json=payload)
    assert response.status_code == 422


def test_checkout_requires_read_back(tmp_path, monkeypatch):
    payload = json.loads(json.dumps(DEMO))
    payload["read_back"] = False
    response = client(tmp_path, monkeypatch).post("/checkout", json=payload)
    assert response.status_code == 409


def test_negative_quantity_is_rejected(tmp_path, monkeypatch):
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"][0]["qty"] = -2
    response = client(tmp_path, monkeypatch).post("/checkout", json=payload)
    assert response.status_code == 422


def test_decision_lookup_hides_the_approval_code(tmp_path, monkeypatch):
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    payload["cart"]["total"] = 52.35
    body = client(tmp_path, monkeypatch).post("/checkout", json=payload)
    assert body.status_code == 200, body.text
    assert body.json()["decision"] == "approve"
    looked = client(tmp_path, monkeypatch).get(f"/decisions/{body.json()['decision_id']}").json()
    assert "code_hash" not in looked.get("approval", {})
    assert "nonce" not in looked.get("approval", {})


def test_passkey_approval_places_the_order_once(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "policy.main.send_signed_order",
        lambda body: {"order_id": "ord_ok", "status": "awaiting_payment", "payment_link": {"url": "http://127.0.0.1:8002/pay/abc"}},
    )
    monkeypatch.setattr(
        "webauthn.verify_authentication_response",
        lambda **kwargs: type("V", (), {"new_sign_count": kwargs["credential_current_sign_count"] + 1})(),
    )
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    created = api.post("/checkout", json=payload).json()
    approval_id = created["approval"]["approval_id"]
    from policy.store import save_caregiver_credential

    save_caregiver_credential({"credential_id": "priya", "public_key": "AQID", "sign_count": 0})
    first = api.post(f"/approvals/{approval_id}/decide", json={"approved": True, "response": {"id": "priya"}})
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "approved"
    assert first.json()["order"]["order_id"] == "ord_ok"
    again = api.post(f"/approvals/{approval_id}/decide", json={"approved": True, "response": {"id": "priya"}})
    assert again.status_code == 400


def test_rejection_closes_the_approval(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    created = api.post("/checkout", json=payload).json()
    approval_id = created["approval"]["approval_id"]
    rejected = api.post(f"/approvals/{approval_id}/decide", json={"approved": False})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["order"] is None
    again = api.post(f"/approvals/{approval_id}/decide", json={"approved": False})
    assert again.status_code == 400


def test_expired_approval_closes(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    created = api.post("/checkout", json=payload).json()
    approval_id = created["approval"]["approval_id"]
    from policy.store import load_decisions, save_decision

    for document in load_decisions().values():
        if (document.get("approval") or {}).get("approval_id") == approval_id:
            document["approval"]["expires_at"] = "2020-01-01T00:00:00+00:00"
            save_decision(document)
    status = api.get(f"/approvals/{approval_id}")
    assert status.status_code == 200
    assert status.json()["state"] == "expired"
    late = api.post(f"/approvals/{approval_id}/decide", json={"approved": False})
    assert late.status_code == 400


def test_approved_order_adds_the_monthly_spend(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "policy.main.send_signed_order",
        lambda body: {"order_id": "ord_ok", "status": "awaiting_payment", "payment_link": {"url": "http://127.0.0.1:8002/pay/abc"}},
    )
    monkeypatch.setattr(
        "webauthn.verify_authentication_response",
        lambda **kwargs: type("V", (), {"new_sign_count": 1})(),
    )
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    created = api.post("/checkout", json=payload).json()
    from policy.store import load_spent_cents, save_caregiver_credential

    before = load_spent_cents()
    save_caregiver_credential({"credential_id": "priya", "public_key": "AQID", "sign_count": 0})
    approved = api.post(f"/approvals/{created['approval']['approval_id']}/decide", json={"approved": True, "response": {"id": "priya"}})
    assert approved.status_code == 200, approved.text
    assert load_spent_cents() == before + 5235


def test_budget_starts_at_the_baseline(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch).get("/budget", params={"mandate_id": "m_ruth_2026_09"})
    assert response.json() == {"monthly_cap": 300.0, "spent": 142.1, "left": 157.9}
