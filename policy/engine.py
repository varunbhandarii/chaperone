"""Mandate rules. Pure: no network, no clock, no files. Money is integer cents."""

from __future__ import annotations

from datetime import date

CATEGORY_MAP = {
    "pantry": "grocery",
    "dairy": "grocery",
    "beverages": "grocery",
    "produce": "grocery",
    "nutrition": "grocery",
    "bakery": "grocery",
    "grocery": "grocery",
    "otc_medicine": "pharmacy",
    "pharmacy_pickup": "pharmacy",
    "pharmacy": "pharmacy",
    "gift_card": "gift_card",
    "prepaid_card": "prepaid_card",
}


def to_cents(value) -> int:
    return int(round(float(value) * 100))


def dollars(cents: int) -> float:
    return round(cents / 100, 2)


def mandate_category(item: dict) -> str:
    if item.get("mandate_category"):
        return item["mandate_category"]
    raw = item.get("category") or ""
    return CATEGORY_MAP.get(raw, raw)


def _rule(rule_id: str, passed: bool, detail: str) -> dict:
    return {"id": rule_id, "passed": passed, "detail": detail}


def evaluate(
    cart: dict,
    mandate: dict,
    monthly_spent_cents: int,
    judge: dict | None = None,
    today: date | None = None,
    *,
    screen_action: str | None = None,
    judge_error: str | None = None,
    signed: bool | None = None,
) -> dict:
    today = today or date.today()
    items = cart.get("items") or []
    total = sum(to_cents(item.get("price", 0)) * int(item.get("qty") or 1) for item in items)
    merchant = cart.get("merchant") or ""
    categories = [mandate_category(item) for item in items]
    blocked = set(mandate.get("blocked_categories") or [])
    allowed_cats = set(mandate.get("allowed_categories") or [])
    cap = to_cents(mandate["per_purchase_cap"])
    monthly_cap = to_cents(mandate["monthly_cap"])
    threshold = to_cents(mandate["approval_threshold"])
    after_allow = monthly_spent_cents + total

    is_signed = bool(mandate.get("passkey")) if signed is None else signed
    try:
        start = date.fromisoformat(str(mandate["valid_from"])[:10])
        end = date.fromisoformat(str(mandate["valid_to"])[:10])
        in_window = start <= today <= end
    except (KeyError, ValueError):
        in_window = False
    r0_ok = is_signed and in_window
    r0_detail = "signed and in force" if r0_ok else "unsigned or outside validity"

    blocked_hit = next((cat for cat in categories if cat in blocked), None)
    r1 = _rule("R1_blocked_category", blocked_hit is None, blocked_hit or "no blocked category")
    r2 = _rule("R2_merchant_allowed", merchant in set(mandate.get("allowed_merchants") or []), merchant or "missing merchant")
    bad_cat = next((cat for cat in categories if cat not in allowed_cats), None)
    r3 = _rule("R3_category_allowed", bad_cat is None, bad_cat or ",".join(categories) or "empty cart")
    r4 = _rule("R4_per_purchase_cap", total <= cap, f"{dollars(total):.2f} <= {dollars(cap):.2f}")
    r5 = _rule("R5_monthly_cap", after_allow <= monthly_cap, f"{dollars(monthly_spent_cents):.2f} + {dollars(total):.2f} <= {dollars(monthly_cap):.2f}")
    r6 = _rule("R6_approval_threshold", total <= threshold, f"{dollars(total):.2f} <= {dollars(threshold):.2f}")

    if judge_error or judge is None:
        r7_passed = screen_action != "judge"
        r7_detail = "judge unavailable" if (judge_error or screen_action == "judge") else "not called"
        refused = False
    else:
        score = max(0.0, min(1.0, float(judge.get("scam_score") or 0)))
        threshold_score = float(judge.get("threshold") or 0.6)
        refused = judge.get("action") == "refuse_and_alert" or score >= threshold_score
        r7_passed = not refused
        r7_detail = f"score {score:.2f}"
    r7 = _rule("R7_scam_judge", r7_passed, r7_detail)
    rules = [
        _rule("R0_mandate_valid", r0_ok, r0_detail),
        r1, r2, r3, r4, r5, r6, r7,
    ]
    hard_fail = (not r0_ok) or any(not rule["passed"] for rule in (r1, r2, r3, r4, r5)) or refused
    if hard_fail:
        decision = "deny"
        spent_after = monthly_spent_cents
    elif screen_action == "judge" and judge is None:
        decision = "approve"
        spent_after = monthly_spent_cents
    elif not r6["passed"]:
        decision = "approve"
        spent_after = monthly_spent_cents
    else:
        decision = "allow"
        spent_after = after_allow

    say = "ordering_now"
    if decision == "approve":
        say = "asking_priya"
    elif decision == "deny":
        say = "blocked_category" if not r1["passed"] else "declined"
    return {
        "decision": decision,
        "rules": rules,
        "monthly_total_after": dollars(spent_after),
        "say_key": say,
        "judge": judge,
        "judge_error": judge_error,
    }
