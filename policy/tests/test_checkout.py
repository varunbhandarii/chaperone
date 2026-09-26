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


def test_host_page_code_approves_and_then_disappears(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "policy.main.send_signed_order",
        lambda body: {"order_id": "ord_code", "status": "awaiting_payment", "payment_link": {"url": "http://127.0.0.1:8002/pay/abc"}},
    )
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    approval_id = api.post("/checkout", json=payload).json()["approval"]["approval_id"]
    proxied = api.get(f"/approvals/{approval_id}/host_code", headers={"X-Forwarded-For": "203.0.113.9"})
    assert proxied.status_code == 403
    code = api.get(f"/approvals/{approval_id}/host_code").json()["code"]
    assert len(code) == 6 and code.isdigit()
    assert code not in json.dumps(api.get(f"/decisions/{api.get(f'/approvals/{approval_id}').json()['decision_id']}").json())
    approved = api.post(f"/approvals/{approval_id}/code", json={"code": code})
    assert approved.status_code == 200, approved.text
    assert approved.json()["order"]["order_id"] == "ord_code"
    assert api.get(f"/approvals/{approval_id}/host_code").status_code == 404


def test_rejection_closes_the_approval(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    created = api.post("/checkout", json=payload).json()
    approval_id = created["approval"]["approval_id"]
    from policy.approvals import reject_marker

    bare = api.post(f"/approvals/{approval_id}/decide", json={"approved": False})
    assert bare.status_code == 400
    rejected = api.post(
        f"/approvals/{approval_id}/decide",
        json={"approved": False, "message": "not this brand"},
        headers={"X-Chaperone-Marker": reject_marker(approval_id)},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["message"] == "not this brand"
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


def test_bad_quantity_is_a_client_error(tmp_path, monkeypatch):
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"][0]["qty"] = "abc"
    response = client(tmp_path, monkeypatch).post("/checkout", json=payload)
    assert response.status_code == 422


def test_unchanged_cart_reuses_the_pending_approval(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    payload["cart"]["total"] = 52.35
    first = api.post("/checkout", json=payload).json()
    second = api.post("/checkout", json=payload).json()
    assert second["approval"]["approval_id"] == first["approval"]["approval_id"]


def test_approval_after_an_ordinary_purchase(tmp_path, monkeypatch):
    # The demo order: medicine and bread first (an allow decision with no approval), then a cart over $40.
    monkeypatch.setattr(
        "policy.checkout.send_signed_order",
        lambda body: {"order_id": "ord_first", "status": "awaiting_payment", "payment_link": {"url": "http://127.0.0.1:8002/pay/abc"}},
    )
    api = client(tmp_path, monkeypatch)
    assert api.post("/checkout", json=DEMO).json()["decision"] == "allow"
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    later = api.post("/checkout", json=payload)
    assert later.status_code == 200, later.text
    assert later.json()["decision"] == "approve" and later.json()["approval"]["approval_id"]


def test_cancel_closes_the_approval(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    created = api.post("/checkout", json=payload).json()
    approval_id = created["approval"]["approval_id"]
    cancelled = api.post(f"/approvals/{approval_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    again = api.post(f"/approvals/{approval_id}/cancel")
    assert again.status_code == 400


def test_merchant_failure_during_approval_is_not_a_500(tmp_path, monkeypatch):
    monkeypatch.setattr("policy.main.send_signed_order", lambda body: (_ for _ in ()).throw(RuntimeError("merchant down")))
    monkeypatch.setattr(
        "webauthn.verify_authentication_response",
        lambda **kwargs: type("V", (), {"new_sign_count": 1})(),
    )
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    created = api.post("/checkout", json=payload).json()
    from policy.store import save_caregiver_credential

    save_caregiver_credential({"credential_id": "priya", "public_key": "AQID", "sign_count": 0})
    result = api.post(f"/approvals/{created['approval']['approval_id']}/decide", json={"approved": True, "response": {"id": "priya"}})
    assert result.status_code == 200, result.text
    assert result.json()["order_error"] == "merchant down"
    assert result.json()["order"] is None


def test_mandate_read_drops_the_assertion(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    from policy.mandate import DEFAULT_MANDATE
    from policy.store import save_mandate

    save_mandate({**DEFAULT_MANDATE, "passkey": {"credential_id": "priya", "public_key": "k", "response": {"id": "priya"}}})
    body = api.get("/mandate").json()
    assert body["signed"] is True
    assert body["credential_id"] == "priya"
    assert "response" not in json.dumps(body)


def test_five_wrong_codes_lock_the_approval(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    approval_id = api.post("/checkout", json=payload).json()["approval"]["approval_id"]
    for _ in range(5):
        assert api.post(f"/approvals/{approval_id}/code", json={"code": "000000"}).status_code == 400
    locked = api.post(f"/approvals/{approval_id}/code", json={"code": "000000"})
    assert locked.status_code == 400
    assert api.get(f"/approvals/{approval_id}").json()["state"] == "rejected"


def test_payment_marker_places_the_order_without_a_webauthn_get(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "policy.main.send_signed_order",
        lambda body: {"order_id": "ord_spc", "status": "awaiting_payment", "payment_link": {"url": "http://127.0.0.1:8002/pay/abc"}},
    )
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    approval_id = api.post("/checkout", json=payload).json()["approval"]["approval_id"]
    from policy.approvals import approve_marker
    from policy.store import save_caregiver_credential

    save_caregiver_credential({"credential_id": "priya", "public_key": "AQID", "sign_count": 0})
    approved = api.post(
        f"/approvals/{approval_id}/decide",
        json={"approved": True, "response": {"id": "priya", "type": "public-key"}, "sign_count": 1},
        headers={"X-Chaperone-Marker": approve_marker(approval_id)},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["order"]["order_id"] == "ord_spc"


def test_forged_marker_cannot_reject(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    approval_id = api.post("/checkout", json=payload).json()["approval"]["approval_id"]
    forged = api.post(
        f"/approvals/{approval_id}/decide",
        json={"approved": False},
        headers={"X-Chaperone-Marker": "0" * 64},
    )
    assert forged.status_code == 400
    assert api.get(f"/approvals/{approval_id}").json()["state"] == "pending"


def test_script_in_a_decline_stays_json_text(tmp_path, monkeypatch):
    from policy.approvals import reject_marker

    api = client(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(DEMO))
    payload["cart"]["items"] = [{"sku": "BAK-001", "name": "bread", "category": "grocery", "qty": 15, "price": 3.49}]
    approval_id = api.post("/checkout", json=payload).json()["approval"]["approval_id"]
    script = "<script>alert(1)</script>"
    rejected = api.post(
        f"/approvals/{approval_id}/decide",
        json={"approved": False, "message": script + ("x" * 200)},
        headers={"X-Chaperone-Marker": reject_marker(approval_id)},
    )
    assert rejected.status_code == 200, rejected.text
    message = rejected.json()["message"]
    assert message.startswith("<script>alert(1)</script>")
    assert len(message) == 140
    assert rejected.headers["content-type"].startswith("application/json")


def test_traversal_approval_ids_are_not_found(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    for raw in ("../.env", "..%2F..%2F.env", "<script>", "a_not-hex-id"):
        assert api.get(f"/approvals/{raw}").status_code == 404
        assert api.post(f"/approvals/{raw}/decide", json={"approved": False}).status_code == 404


def test_budget_starts_at_the_baseline(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch).get("/budget", params={"mandate_id": "m_ruth_2026_09"})
    assert response.json() == {"monthly_cap": 300.0, "spent": 142.1, "left": 157.9}


def test_reset_needs_the_host_header(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)  # temp store paths, so the real sessions/ files stay untouched
    assert test_client.post("/reset").status_code == 403
    assert test_client.post("/reset", headers={"X-Chaperone-Host": "1"}).status_code == 200
