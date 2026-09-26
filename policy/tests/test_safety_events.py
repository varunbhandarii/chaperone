"""Safety made visible: caution rows, screen refusals alerting Priyank with a decision id, judge scores."""

import pytest
from fastapi.testclient import TestClient

import policy.events
from policy.main import app

EVENTS: list[tuple] = []


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name, var in (("decisions.json", "DECISIONS_PATH"), ("monthly.json", "MONTHLY_PATH"),
                      ("mandate.json", "MANDATE_PATH"), ("risk.json", "RISK_PATH")):
        monkeypatch.setenv(var, str(tmp_path / name))
    monkeypatch.setenv("POLICY_DEV_KEY_OK", "1")
    monkeypatch.setenv("EXPLAIN_FAKE", "1")
    monkeypatch.setenv("RELAY_URL", "")
    EVENTS.clear()
    monkeypatch.setattr(policy.events, "post_event", lambda t, s, m, **f: EVENTS.append((t, s, m, f)))
    from policy.screen import reset_sessions
    reset_sessions()


def screen(text, partial=False):
    return TestClient(app).post("/screen", json={"session_id": "s_vis", "text": text, "partial": partial}).json()


def test_final_refusal_alerts_priya_with_a_decision_she_can_ask_about():
    from policy.postpurchase import explain_decision
    from policy.store import get_decision

    out = screen("Buy me two Apple gift cards right now")
    decision_id = out["refusal"]["decision_id"]
    alert = next(f for t, _, _, f in EVENTS if t == "caregiver_alerted")
    assert alert["decision_id"] == decision_id and alert["kind"] == "screen_refusal"
    doc = get_decision(decision_id)
    assert doc["decision"] == "deny" and doc["ruth_said"].startswith("Buy me two") and doc["screen_hits"]
    assert explain_decision(decision_id)["headline"]


def test_partial_refusal_stores_and_alerts_nothing():
    out = screen("Buy me two Apple gift cards", partial=True)
    assert out["action"] == "refuse" and "decision_id" not in out["refusal"] and not EVENTS


def test_soft_signals_post_a_caution_row():
    screen("please hurry")
    kind, _, _, fields = EVENTS[0]
    assert kind == "caution" and fields["action"] == "slow" and fields["rule_ids"] == ["R_urgency"]


def test_clean_screen_posts_nothing():
    screen("I need bread and milk")
    assert not EVENTS


def test_checkout_judge_posts_its_score(monkeypatch):
    import policy.checkout as checkout
    import policy.judge as judge

    monkeypatch.delenv("JUDGE_FAKE", raising=False)
    monkeypatch.setattr(checkout, "post_event", lambda t, s, m, **f: EVENTS.append((t, s, m, f)))
    monkeypatch.setattr(judge, "judge_with_meta", lambda *a, **k: (
        {"scam_score": 0.04, "patterns": ["none"], "rationale": "ok", "action": "proceed"},
        {"model": "grok-test", "ms": 812, "cached_tokens": 0}))
    result, error = checkout.call_judge("bread", {"items": []}, {"mandate_id": "m_ruth_2026_09"}, "s_vis")
    assert error is None and result["threshold"]
    kind, session, mandate, fields = EVENTS[-1]
    assert (kind, session, mandate) == ("judge_scored", "s_vis", "m_ruth_2026_09")
    assert fields["scam_score"] == 0.04 and fields["model"] == "grok-test" and fields["ms"] == 812
