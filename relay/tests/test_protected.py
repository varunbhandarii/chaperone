from fastapi.testclient import TestClient

from relay import ledger, protected


def ev(type_, **kw):
    return {"type": type_, "session_id": kw.pop("session_id", "s1"), "mandate_id": "m", "t": 1, "source": "policy", **kw}


def test_counts_card_refusals_and_scams_once_each():
    events = [
        ev("card_decision", result="declined", reason_key="card_cooldown", amount=480, store="Five Points Drug"),
        ev("card_decision", result="declined", reason_key="card_over_cap", amount=90),     # her own choice
        ev("card_decision", result="declined", reason_key="card_blocked_category", amount=50),
        ev("card_decision", result="approved", amount=20),
        ev("policy_decision", decision="deny", decision_id="d1", rules_failed=["R1_blocked_category"], total="200.00"),
        ev("refusal", decision_id="d1", rule_ids=["R1_blocked_category"]),                   # same stop
        ev("policy_decision", decision="deny", decision_id="d2", rules_failed=["R5_monthly_cap"], total="30.00"),
        ev("scam_checked", verdict="scam", check_id="sc1", amount=480),
        ev("scam_checked", verdict="likely_safe", check_id="sc2"),
        ev("caregiver_alerted", kind="screen_refusal", decision_id="d3", session_id="s2"),
        ev("refusal", rule_id="S_screen_family_emergency", session_id="s2"),                # the station's copy
    ]
    out = protected.compute(events)
    assert out["dollars"] == "1210.00"   # 480 + 50 + 200 + 480
    assert out["card_declines"] == 3
    assert out["scams_stopped"] == 3     # sc1, d1, d3 (d2 is a budget cap, not a scam)
    assert {row["guard"] for row in out["rows"]} == {"card", "agent", "ask"}


def test_empty_ledger_is_zero():
    assert protected.compute([]) == {"dollars": "0.00", "scams_stopped": 0, "card_declines": 0, "rows": []}


def test_route_reads_the_live_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER", ledger.Ledger(tmp_path / "live.jsonl"))
    client = TestClient(ledger.app)
    client.post("/events", json=ev("card_decision", result="declined", reason_key="card_cooldown", amount=480,
                                   store="Five Points Drug", card_last4="1111", mcc="5912"))
    r = client.get("/wall/data/protected")
    assert r.status_code == 200 and r.json()["dollars"] == "480.00" and r.json()["card_declines"] == 1
