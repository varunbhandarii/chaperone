import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from policy.approvals import action_marker
from policy.engine import evaluate
from policy.mandate import DEFAULT_MANDATE
from policy.store import add_spent_cents, load_mandate, load_spent_cents, save_decision, save_mandate
from policy.tests.test_checkout import DEMO, client
from policy.tests.test_engine import item, proceed, run


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
    monkeypatch.setattr("policy.postpurchase.signed_post", lambda path, body: {"status": "cancelled", "link_status": "INACTIVE"})
    api = client(tmp_path, monkeypatch)
    add_spent_cents(4995)
    before = load_spent_cents()
    _plant("ord_open", "awaiting_payment")
    denied = api.post("/orders/ord_open/cancel", json={"mandate_id": "someone_else"})
    assert denied.status_code == 403
    cancelled = api.post("/orders/ord_open/cancel", json={"mandate_id": "m_ruth_2026_09"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
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
        lambda path, body: {"id": "ref_1", "status": "PENDING", "refundAmountDetails": {"refundAmount": body["amount"], "currency": "USD"}},
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
    assert rx["say"] == "refund_not_allowed_rx"
    preview = api.post("/refunds", json={"order_id": "ord_back", "mandate_id": "m_ruth_2026_09", "sku": "NUT-003", "qty": 1, "confirmed": False, "transcript": "one was dented"}).json()
    assert preview["ok"] is True and preview["preview"]["amount"] == 9.99
    assert {rule["id"] for rule in preview["rules"]} == {f"RF{n}_" + name for n, name in (
        (1, "order_owned"), (2, "amount_remaining"), (3, "original_card"), (4, "return_window"), (5, "screen"), (6, "caregiver_told"),
    )}
    early = api.post("/refunds", json={"order_id": "ord_back", "mandate_id": "m_ruth_2026_09", "sku": "NUT-003", "qty": 2, "confirmed": True, "transcript": "yes"})
    assert early.json()["ok"] is False
    done = api.post("/refunds", json={"order_id": "ord_back", "mandate_id": "m_ruth_2026_09", "sku": "NUT-003", "qty": 1, "confirmed": True, "transcript": "yes"}).json()
    assert done["ok"] is True and done["say"] == "refund_done"


def test_pause_blocks_checkout_words_and_explain_is_cached(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    save_mandate(dict(DEFAULT_MANDATE))
    refused = api.post("/mandate/pause")
    assert refused.status_code == 401
    paused = api.post("/mandate/pause", headers={"X-Chaperone-Marker": action_marker("mandate", "pause")})
    assert paused.status_code == 200, paused.text
    result = evaluate({"merchant": "corner_market", "items": [item(3)]}, load_mandate(), 14210, judge=proceed(), today=datetime.now(timezone.utc).date())
    assert result["say_key"] == "agent_paused"
    _plant("ord_hist", "paid")
    listed = api.get("/history", params={"mandate_id": "m_ruth_2026_09", "days": 30}).json()
    assert listed["orders"][0]["order_id"] == "ord_hist"
    save_decision({"decision_id": "d_why", "mandate_id": "m_ruth_2026_09", "session_id": "s", "decision": "deny", "say_key": "blocked_category", "rules": [{"id": "R1_blocked_category", "passed": False, "detail": "gift cards are blocked on this account"}]})
    explained = api.get("/decisions/d_why/explain").json()
    assert explained["headline"] == "Explained without calling the model"
    assert "R1_blocked_category" in explained["rule_in_plain_words"]
    assert api.get("/decisions/d_why/explain").json() == explained


def test_old_refund_is_outside_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr("policy.screen.screen", lambda *args, **kwargs: {"action": "proceed", "hits": []})
    api = client(tmp_path, monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    _plant("ord_old", "paid", paid_at=old)
    body = api.post("/refunds", json={"order_id": "ord_old", "mandate_id": "m_ruth_2026_09", "confirmed": False, "transcript": "return it"}).json()
    assert any(rule["id"] == "RF4_return_window" and not rule["passed"] for rule in body["rules"])
