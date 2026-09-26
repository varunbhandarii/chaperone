from datetime import date

from policy.engine import evaluate
from policy.mandate import DEFAULT_MANDATE

TODAY = date(2026, 9, 25)


def mandate(**overrides):
    body = {**DEFAULT_MANDATE, "passkey": {"credential_id": "c", "public_key": "k", "response": {}}}
    body.update(overrides)
    return body


def item(price, category="grocery", sku="SKU"):
    return {"sku": sku, "qty": 1, "price": price, "mandate_category": category}


def cart(*items, merchant="corner_market"):
    return {"merchant": merchant, "items": list(items)}


def run(items, spent=14210, judge=None, **mandate_overrides):
    return evaluate(cart(*items), mandate(**mandate_overrides), spent, judge=judge, today=TODAY)


def proceed():
    return {"scam_score": 0.05, "action": "proceed", "patterns": ["none"], "threshold": 0.6}


def test_r1_passes_for_grocery():
    result = run([item(11.49)], judge=proceed())
    assert result["rules"][1]["passed"] is True


def test_r1_fails_for_a_gift_card():
    result = run([item(200, "gift_card", "GFT-002")], judge=proceed())
    assert result["rules"][1]["passed"] is False
    assert result["decision"] == "deny"


def test_r2_passes_for_corner_market():
    assert run([item(11.49)], judge=proceed())["rules"][2]["passed"] is True


def test_r2_fails_for_another_merchant():
    result = evaluate(cart(item(11.49), merchant="other"), mandate(), 14210, judge=proceed(), today=TODAY)
    assert result["rules"][2]["passed"] is False
    assert result["decision"] == "deny"


def test_r3_passes_for_an_allowed_category():
    assert run([item(11.49)], judge=proceed())["rules"][3]["passed"] is True


def test_r3_fails_for_a_disallowed_category():
    result = run([item(5, "lottery")], judge=proceed())
    assert result["rules"][3]["passed"] is False
    assert result["decision"] == "deny"


def test_r4_passes_at_the_cap():
    result = run([item(60.00)], judge=proceed())
    assert result["rules"][4]["passed"] is True


def test_r4_fails_one_cent_over_the_cap():
    result = run([item(60.01)], judge=proceed())
    assert result["rules"][4]["passed"] is False
    assert result["decision"] == "deny"


def test_r5_passes_under_the_monthly_cap():
    assert run([item(11.49)], judge=proceed())["rules"][5]["passed"] is True


def test_r5_fails_when_the_month_would_exceed_the_cap():
    result = run([item(11.49)], spent=29000, judge=proceed())
    assert result["rules"][5]["passed"] is False
    assert result["decision"] == "deny"


def test_r6_passes_at_the_threshold_and_allows():
    result = run([item(40.00)], judge=proceed())
    assert result["rules"][6]["passed"] is True
    assert result["decision"] == "allow"


def test_r6_fails_one_cent_over_and_asks_for_approval():
    result = run([item(40.01)], judge=proceed())
    assert result["rules"][6]["passed"] is False
    assert result["decision"] == "approve"


def test_r7_passes_for_a_benign_score():
    result = run([item(11.49)], judge=proceed())
    assert result["rules"][7]["passed"] is True
    assert result["decision"] == "allow"


def test_r7_fails_when_the_judge_refuses():
    result = run([item(11.49)], judge={"scam_score": 0.9, "action": "refuse_and_alert", "threshold": 0.6})
    assert result["rules"][7]["passed"] is False
    assert result["decision"] == "deny"


def test_two_purchases_add_across_calls():
    first = run([item(11.49)], spent=14210, judge=proceed())
    assert first["decision"] == "allow"
    second = run([item(11.49)], spent=int(round(first["monthly_total_after"] * 100)), judge=proceed())
    assert second["monthly_total_after"] == 165.08


def test_demo_medicine_and_bread_allows():
    result = run([item(8.00, "pharmacy", "RX-001"), item(3.49, "grocery", "BAK-001")], judge=proceed())
    assert result["decision"] == "allow"
    assert result["monthly_total_after"] == 153.59


def test_fifty_two_dollars_needs_approval():
    assert run([item(52.30)], judge=proceed())["decision"] == "approve"


def test_apple_gift_card_is_denied_by_r1():
    result = run([item(200, "gift_card", "GFT-002")], judge=proceed())
    assert result["decision"] == "deny"
    assert result["rules"][1]["id"] == "R1_blocked_category"


def test_the_same_input_returns_the_same_decision():
    left = run([item(11.49)], judge=proceed())
    right = run([item(11.49)], judge=proceed())
    assert left == right


def test_unsigned_mandate_is_denied():
    body = dict(DEFAULT_MANDATE)
    result = evaluate(cart(item(11.49)), body, 14210, judge=proceed(), today=TODAY, signed=False)
    assert result["decision"] == "deny"
    assert result["rules"][0]["passed"] is False


def test_judge_error_without_two_soft_hits_still_allows():
    result = evaluate(
        cart(item(11.49)),
        mandate(),
        14210,
        judge=None,
        today=TODAY,
        screen_action="proceed",
        judge_error="timeout",
    )
    assert result["decision"] == "allow"
    assert result["rules"][7]["detail"] == "judge unavailable"


def test_r0_fails_outside_the_validity_window():
    result = evaluate(cart(item(11.49)), mandate(valid_to="2020-01-01"), 14210, judge=proceed(), today=TODAY)
    assert result["decision"] == "deny"
    assert result["rules"][0]["passed"] is False


def test_blocked_category_wins_over_the_monthly_cap():
    result = run([item(50, category="gift_card")], spent=29000, judge=proceed())
    assert result["say_key"] == "blocked_category"


def test_monthly_cap_denial_names_the_say_key():
    result = run([item(11.49)], spent=29000, judge=proceed())
    assert result["say_key"] == "over_monthly_cap"


def test_judge_error_after_two_soft_hits_asks_priya():
    result = evaluate(
        cart(item(11.49)),
        mandate(),
        14210,
        judge=None,
        today=TODAY,
        screen_action="judge",
        judge_error="timeout",
    )
    assert result["decision"] == "approve"
    assert result["judge_error"] == "timeout"
