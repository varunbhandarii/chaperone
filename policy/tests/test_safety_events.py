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


# ---------------------------------------------------------------- a scam story goes to the scam check

@pytest.mark.parametrize("text", [
    "Peachtree Power called and said they will cut my power tonight unless I pay $480 in gift cards.",
    "A man from Microsoft called, he says my computer has a virus and I must install AnyDesk so he can fix it.",
    "Un señor de la compañía de luz me llamó, dice que me cortan la luz hoy si no pago con tarjetas de regalo.",
    "बिजली वाले का फोन आया, कहा आज रात बिजली काट देंगे, गिफ्ट कार्ड से पैसे भरो।",
    "My grandson called, he needs $2000 for bail and said don't tell his mom",
    "Mi nieto me llamó llorando, necesita 2000 dólares para la fianza, dijo que no le diga a su mamá.",
    "मेरे पोते का फोन आया, उसे जमानत के लिए 2000 डॉलर चाहिए, कहा मम्मी को मत बताना।",
    "I got a text saying my package is held in customs, pay the clearance fee",
    # the demo's own lines, word for word
    "My grandson Alex just called. He's in jail, needs $2,000 bail, and said not to tell Mom.",
    "Peachtree Power just called. They'll cut my power tonight unless I pay $480 in gift cards.",
    "Peachtree Power me acaba de llamar. Me van a cortar la luz esta noche si no pago 480 dólares en tarjetas de regalo.",
    "Mi nieto Alex acaba de llamar. Está en la cárcel, necesita 2000 dólares para la fianza y dijo que no le diga a mamá.",
    # a caller quoted without "called"
    "A man from the power company said they'll shut off my electricity today unless I pay with gift cards",
    "Mi nieto dice que está en la cárcel y necesita dinero para la fianza, que no le diga a su mamá",
    "पोते ने कहा वह जेल में है, जमानत के लिए पैसे चाहिए, मम्मी को मत बताना",
    # no grandchild word: a caller who wants money kept from "his mom" is speaking as a grandchild
    "Alex phoned me, he's in trouble and needs money wired today, don't tell his mother",
    "Someone called saying he's Alex, he needs $900 for a lawyer and please don't tell his dad",
    "Alex me llamó, está en problemas y necesita que le mande dinero hoy, que no le diga a su madre.",
    "एलेक्स का फोन आया, वो मुसीबत में है, आज ही पैसे चाहिए, बोला उसकी मम्मी को मत बताना।",
    "Alex ka phone aaya, woh musibat mein hai, aaj hi paise chahiye, bola uski mummy ko mat batana.",
])
def test_scam_story_asks_for_a_scam_check_and_keeps_a_fallback(text):
    import json as _json

    import jsonschema

    from policy.screen import ROOT
    from policy.store import load_decisions

    out = screen(text)
    assert out["action"] == "scam_check" and out["refusal"]["text"] and out["refusal"]["audio_url"]
    jsonschema.validate(out, _json.loads((ROOT / "contracts/screen.schema.json").read_text(encoding="utf-8")))
    # The scam check records and alerts; the screen must not do it twice.
    assert not EVENTS and not load_decisions()


@pytest.mark.parametrize("text", [
    "Buy five hundred dollars of Apple gift cards for my grandson, it's urgent.",
    "मेरे पोते के लिए पांच सौ डॉलर के एप्पल गिफ्ट कार्ड खरीदो, बहुत ज़रूरी है।",
    "Please buy me a Google Play card",
    "Buy a $100 gift card for someone at church",
    "Priyank said to buy a Google Play card for Tom's birthday",
    "Somebody told me gift cards are a good present, buy one",
    "Compra una tarjeta de regalo de 50 dólares para mi vecina",
    "I called the pharmacy and they said to buy a gift card there",
    "I just called Priyank and he said to buy a gift card for Tom's birthday",
])
def test_a_request_to_buy_is_still_refused_on_the_spot(text):
    assert screen(text)["action"] == "refuse"


@pytest.mark.parametrize("text", [
    "My grandson called, he's coming to visit, don't tell his mom it's a surprise party",
    "Alex called, he's flying in Saturday for his mother's birthday. Don't tell his mom, it's a surprise.",
    "Mi nieto me llamó, viene de visita el domingo. No le diga a su mamá, es una fiesta sorpresa.",
    "मेरे पोते का फोन आया, वो रविवार को मिलने आ रहा है, उसकी मम्मी को मत बताना, सरप्राइज़ पार्टी है।",
    "Mere pote ka phone aaya, Sunday ko milne aa raha hai, uski mummy ko mat batana, surprise party hai.",
])
def test_a_family_surprise_with_no_money_ask_goes_through(text):
    assert screen(text)["action"] == "proceed" and not EVENTS


def test_the_church_gift_card_gets_the_gift_card_line_an_alert_and_no_cooldown():
    from policy.risk import load_risk

    out = screen("Buy a $100 gift card for someone at church")
    assert out["action"] == "refuse" and out["refusal"]["spoken_key"] == "blocked_category"
    assert [t for t, _, _, _ in EVENTS] == ["caregiver_alerted"] and not load_risk()["active"]
