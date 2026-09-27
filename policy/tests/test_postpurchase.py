import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from merchant.orders import CancelRequest, RefundRequest
from ai.explain import FAKE, problems
from policy.approvals import action_marker
from policy.checkout import active_mandate
from policy.engine import evaluate
from policy.mandate import DEFAULT_MANDATE
from policy.store import add_spent_cents, load_mandate, load_spent_cents, save_decision, save_mandate
from policy.tests.test_checkout import DEMO, client
from policy.tests.test_engine import item, proceed, run


@pytest.fixture(autouse=True)
def no_merchant(monkeypatch):
    monkeypatch.setattr("policy.postpurchase._merchant_orders", lambda order_id=None: None)


def _plant(order_id, status, **extra):
    document = {
        "decision_id": "d_" + order_id,
        "mandate_id": "m_ruth_2026_09",
        "session_id": "s_hist",
        "decision": "allow",
        "say_key": "ordering_now",
        "rules": [{"id": "R0_mandate_valid", "passed": True, "detail": "signed"}],
        "cart": {"merchant": "corner_market", "total": 49.95, "items": [{"sku": "NUT-003", "name": "Ensure", "qty": 5, "price": 9.99, "category": "grocery"}]},
        "order": {"order_id": order_id, "status": status, "paid_at": datetime.now(timezone.utc).isoformat(), **extra},
    }
    save_decision(document)
    return document


def test_cancel_requires_ownership_and_restores_spend(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("policy.postpurchase.signed_post", lambda path, body: sent.append(body) or {"status": "cancelled", "link_status": "INACTIVE"})
    api = client(tmp_path, monkeypatch)
    add_spent_cents(4995)
    before = load_spent_cents()
    _plant("ord_open", "awaiting_payment")
    denied = api.post("/orders/ord_open/cancel", json={"mandate_id": "someone_else"})
    assert denied.status_code == 403
    cancelled = api.post("/orders/ord_open/cancel", json={"mandate_id": "m_ruth_2026_09"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    CancelRequest.model_validate(sent[0])  # the merchant's own model for the signed body
    assert load_spent_cents() == before - 4995
    late = api.post("/orders/ord_open/cancel", json={"mandate_id": "m_ruth_2026_09"})
    assert late.status_code == 409


def test_paid_order_cannot_be_cancelled(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    _plant("ord_paid", "paid")
    assert api.post("/orders/ord_paid/cancel", json={"mandate_id": "m_ruth_2026_09"}).status_code == 409


def test_refund_rules_cover_preview_rx_and_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "policy.postpurchase.signed_post",
        lambda path, body: RefundRequest.model_validate(body) and {"id": "ref_1", "status": "PENDING", "refundAmountDetails": {"refundAmount": body["amount"], "currency": "USD"}},
    )
    monkeypatch.setattr("policy.screen.screen", lambda *args, **kwargs: {"action": "proceed", "hits": []})
    api = client(tmp_path, monkeypatch)
    _plant("ord_back", "paid")
    save_decision({
        "decision_id": "d_rx",
        "mandate_id": "m_ruth_2026_09",
        "session_id": "s_hist",
        "decision": "allow",
        "cart": {"total": 8, "items": [{"sku": "RX-001", "name": "pickup", "qty": 1, "price": 8, "category": "pharmacy_pickup"}]},
        "order": {"order_id": "ord_rx", "status": "paid", "paid_at": datetime.now(timezone.utc).isoformat()},
    })
    rx = api.post("/refunds", json={"order_id": "ord_rx", "mandate_id": "m_ruth_2026_09", "sku": "RX-001", "qty": 1, "confirmed": False, "transcript": "return it"}).json()
    assert any(rule["id"] == "RF4_return_window" and not rule["passed"] for rule in rx["rules"])
    assert rx["say_key"] == "refund_not_allowed_rx" and "preview" not in rx
    preview = api.post("/refunds", json={"order_id": "ord_back", "mandate_id": "m_ruth_2026_09", "sku": "NUT-003", "qty": 1, "confirmed": False, "transcript": "one was dented"}).json()
    assert preview["ok"] is True and preview["preview"]["amount"] == 9.99
    assert {rule["id"] for rule in preview["rules"]} == {f"RF{n}_" + name for n, name in (
        (1, "order_owned"), (2, "amount_remaining"), (3, "original_card"), (4, "return_window"), (5, "screen"), (6, "caregiver_told"),
    )}
    early = api.post("/refunds", json={"order_id": "ord_back", "mandate_id": "m_ruth_2026_09", "sku": "NUT-003", "qty": 2, "confirmed": True, "transcript": "yes"})
    assert early.json()["ok"] is False
    done = api.post("/refunds", json={"order_id": "ord_back", "mandate_id": "m_ruth_2026_09", "sku": "NUT-003", "qty": 1, "confirmed": True, "transcript": "yes"}).json()
    assert done["ok"] is True and done["say_key"] == "refund_done"
    assert "preview" not in done and done["refund"]["status"] == "PENDING"
    again = api.post("/refunds", json={"order_id": "ord_back", "mandate_id": "m_ruth_2026_09", "sku": "NUT-003", "qty": 5, "confirmed": False, "transcript": "all of them"}).json()
    assert again["decision"] == "deny" and "4 of Ensure left" in next(r for r in again["rules"] if r["id"] == "RF2_amount_remaining")["detail"]


def test_pause_blocks_checkout_words_and_explain_is_cached(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    save_mandate(dict(DEFAULT_MANDATE))
    refused = api.post("/mandate/pause")
    assert refused.status_code == 401
    paused = api.post("/mandate/pause", headers={"X-Chaperone-Marker": action_marker("mandate", "pause")})
    assert paused.status_code == 200, paused.text
    signed_before = json.dumps(load_mandate(), sort_keys=True)
    result = evaluate({"merchant": "corner_market", "items": [item(3)]}, active_mandate()[0], 14210, judge=proceed(), today=datetime.now(timezone.utc).date())
    assert result["say_key"] == "agent_paused"
    # the pause is kept apart: the mandate the passkey signed, and its hash, do not change
    assert json.dumps(load_mandate(), sort_keys=True) == signed_before
    assert api.get("/mandate").json()["paused"] is True
    _plant("ord_hist", "paid")
    listed = api.get("/history", params={"mandate_id": "m_ruth_2026_09", "days": 30}).json()
    assert listed["orders"][0]["order_id"] == "ord_hist"
    save_decision({"decision_id": "d_why", "mandate_id": "m_ruth_2026_09", "session_id": "s", "decision": "deny", "say_key": "blocked_category", "rules": [{"id": "R1_blocked_category", "passed": False, "detail": "gift cards are blocked on this account"}]})
    explained = api.get("/decisions/d_why/explain", headers={"X-Chaperone-Marker": action_marker("d_why", "explain")}).json()
    assert explained == FAKE and not problems(explained)
    assert api.get("/decisions/d_why/explain", headers={"X-Chaperone-Marker": action_marker("d_why", "explain")}).json() == explained
    assert api.get("/decisions/d_why/explain", headers={"x-forwarded-for": "8.8.8.8"}).status_code == 403
    assert api.get("/history", headers={"x-forwarded-for": "8.8.8.8"}).status_code == 403
    assert api.post("/refunds", headers={"x-forwarded-for": "8.8.8.8"}, json={"order_id": "ord_hist"}).status_code == 403
    assert api.post("/orders/ord_hist/cancel", headers={"x-forwarded-for": "8.8.8.8"}, json={"mandate_id": "m_ruth_2026_09"}).status_code == 403


def test_old_refund_is_outside_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr("policy.screen.screen", lambda *args, **kwargs: {"action": "proceed", "hits": []})
    api = client(tmp_path, monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    _plant("ord_old", "paid", paid_at=old)
    body = api.post("/refunds", json={"order_id": "ord_old", "mandate_id": "m_ruth_2026_09", "confirmed": False, "transcript": "return it"}).json()
    assert any(rule["id"] == "RF4_return_window" and not rule["passed"] for rule in body["rules"])


def test_refund_reads_the_merchant_status_policy_never_saw(tmp_path, monkeypatch):
    monkeypatch.setattr("policy.screen.screen", lambda *args, **kwargs: {"action": "proceed", "hits": []})
    api = client(tmp_path, monkeypatch)
    _plant("ord_live", "awaiting_payment", paid_at=None)
    paid = datetime.now(timezone.utc).timestamp() - 60
    monkeypatch.setattr(
        "policy.postpurchase._merchant_orders",
        lambda order_id=None: [{"order_id": "ord_live", "status": "ready_for_pickup", "paid_at": paid, "card_last4": "1111"}],
    )
    body = api.post("/refunds", json={"order_id": "ord_live", "mandate_id": "m_ruth_2026_09", "sku": "NUT-003", "qty": 1, "confirmed": False, "transcript": "one was dented"}).json()
    assert body["ok"] is True, body["rules"]
    assert body["preview"]["card_last4"] == "1111"
    assert api.post("/orders/ord_live/cancel", json={"mandate_id": "m_ruth_2026_09"}).status_code == 409
    listed = api.get("/history", params={"mandate_id": "m_ruth_2026_09"}).json()
    assert listed["orders"][0]["status"] == "ready_for_pickup"


def test_resume_needs_the_marker_and_a_live_single_use_challenge(tmp_path, monkeypatch):
    from policy.main import app
    from policy.store import save_caregiver_credential

    api = client(tmp_path, monkeypatch)
    save_mandate(dict(DEFAULT_MANDATE))
    save_caregiver_credential({"credential_id": "c", "public_key": "AQ", "sign_count": 0})
    marker = {"X-Chaperone-Marker": action_marker("mandate", "pause")}
    api.post("/mandate/pause", headers=marker)
    challenge = api.post("/mandate/resume/challenge", headers=marker).json()
    assert api.post("/mandate/resume", json={"nonce": challenge["nonce"], "response": {}}).status_code == 401
    assert api.post("/mandate/resume", json={"nonce": challenge["nonce"], "response": {}}, headers=marker).status_code == 400
    # the failed try consumed it
    again = api.post("/mandate/resume", json={"nonce": challenge["nonce"], "response": {}}, headers=marker)
    assert again.json()["detail"] == "resume challenge missing"
    challenge = api.post("/mandate/resume/challenge", headers=marker).json()
    app.state.resume["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    late = api.post("/mandate/resume", json={"nonce": challenge["nonce"], "response": {}}, headers=marker)
    assert late.json()["detail"] == "resume challenge expired"
    assert api.get("/mandate").json()["paused"] is True


def _plant_split(status="awaiting_payment"):
    """Medicine at the pharmacy and bread at the grocery: two orders under one decision."""
    now = datetime.now(timezone.utc).isoformat()
    rx = {"order_id": "ord_rx", "status": status, "merchant": "parkside_pharmacy", "total": 8.0, "paid_at": now}
    bread = {"order_id": "ord_bread", "status": status, "merchant": "corner_market", "total": 3.49, "paid_at": now}
    document = {
        "decision_id": "d_split", "mandate_id": "m_ruth_2026_09", "session_id": "s_split", "decision": "allow",
        "created_at": now,
        "cart": {"merchant": "corner_market", "total": 11.49, "items": [
            {"sku": "RX-001", "name": "Lisinopril", "qty": 1, "price": 8.0, "category": "pharmacy_pickup", "merchant": "parkside_pharmacy"},
            {"sku": "BAK-001", "name": "Bread", "qty": 1, "price": 3.49, "category": "grocery", "merchant": "corner_market"}]},
        "order": dict(rx), "orders": [rx, bread],
    }
    save_decision(document)
    return document


def test_split_orders_cancel_refund_and_history_work_per_store(tmp_path, monkeypatch):
    monkeypatch.setattr("policy.postpurchase.signed_post", lambda path, body: {"status": "cancelled", "link_status": "INACTIVE"}
                        if path.endswith("/cancel") else {"id": "ref_1", "status": "PENDING"})
    monkeypatch.setattr("policy.screen.screen", lambda *args, **kwargs: {"action": "proceed", "hits": []})
    api = client(tmp_path, monkeypatch)
    _plant_split()
    add_spent_cents(1149)
    before = load_spent_cents()
    # cancelling the bread order restores only the bread's share
    assert api.post("/orders/ord_bread/cancel", json={"mandate_id": "m_ruth_2026_09"}).status_code == 200
    assert load_spent_cents() == before - 349
    rows = {o["order_id"]: o for o in api.get("/history", params={"mandate_id": "m_ruth_2026_09"}).json()["orders"]}
    assert rows["ord_bread"]["status"] == "cancelled" and rows["ord_rx"]["status"] == "awaiting_payment"
    assert rows["ord_rx"]["items"] == ["1 x Lisinopril"] and rows["ord_rx"]["total"] == 8.0
    # a refund on a split order sees only its own store's lines
    _plant_split(status="paid")
    body = api.post("/refunds", json={"order_id": "ord_bread", "mandate_id": "m_ruth_2026_09", "sku": "BAK-001",
                                      "qty": 1, "confirmed": False, "transcript": "the bread was stale"}).json()
    assert body.get("ok") is True and body["preview"]["amount"] == 3.49, body
    wrong_store = api.post("/refunds", json={"order_id": "ord_bread", "mandate_id": "m_ruth_2026_09", "sku": "RX-001",
                                             "qty": 1, "confirmed": False, "transcript": "return it"}).json()
    assert wrong_store["decision"] == "deny"


def test_history_names_the_card_each_order_and_refund_used(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    document = _plant_split(status="paid")
    for entry in document["orders"]:
        entry["card_last4"] = "1111"
    document["refunds"] = [{"order_id": "ord_bread", "sku": "BAK-001", "qty": 1, "amount_cents": 349, "status": "PENDING"}]
    save_decision(document)
    body = api.get("/history", params={"mandate_id": "m_ruth_2026_09"}).json()
    assert {o["order_id"]: o["card_last4"] for o in body["orders"]} == {"ord_rx": "1111", "ord_bread": "1111"}
    assert body["refunds"][0]["card_last4"] == "1111"
