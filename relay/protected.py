"""Protected dollars: what Chaperone kept from leaving Ruth's money since the last reset.

    GET /wall/data/protected -> {dollars, scams_stopped, card_declines, rows[]}

Counted from the live ledger (it is truncated by /reset):
  * a declined card_decision whose reason is a blocked category, the cool-down or an unusual amount;
  * a refused checkout (policy_decision deny) whose failed rules came from the screen, the judge or a blocked
    category, at its cart total;
  * a scam_checked with verdict scam, at the amount the scam check extracts from the story (0 when none).
scams_stopped counts scam verdicts and refusals (each decision once, whoever posted it); card_declines counts
every declined swipe. Declines for being over a cap are not "protected": the money was Ruth's own choice.
"""

from __future__ import annotations

PROTECTING_CARD_REASONS = {"card_blocked_category", "card_cooldown", "card_unusual_amount"}
PROTECTING_RULE_PREFIXES = ("R1_", "R7_", "S_screen_")


def _money(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _protecting_refusal(event: dict) -> bool:
    return any(str(rule).startswith(PROTECTING_RULE_PREFIXES) for rule in event.get("rules_failed") or [])


def compute(events: list[dict]) -> dict:
    rows: list[dict] = []
    refusals: set[str] = set()
    anonymous_refusal_sessions: set[str] = set()
    screen_alert_sessions: set[str] = set()
    scams = card_declines = 0
    for event in events:
        kind = event.get("type")
        if kind == "card_decision" and event.get("result") == "declined":
            card_declines += 1
            if event.get("reason_key") in PROTECTING_CARD_REASONS:
                rows.append({"guard": "card", "amount": _money(event.get("amount")), "store": event.get("store"),
                             "reason_key": event.get("reason_key"), "seq": event.get("seq")})
        elif kind == "policy_decision" and event.get("decision") == "deny" and _protecting_refusal(event):
            decision_id = event.get("decision_id") or f"seq{event.get('seq')}"
            if decision_id not in refusals:
                refusals.add(decision_id)
                rows.append({"guard": "agent", "amount": _money(event.get("total")),
                             "rules": event.get("rules_failed"), "decision_id": event.get("decision_id"),
                             "seq": event.get("seq")})
        elif kind == "refusal":
            if event.get("decision_id"):
                refusals.add(event["decision_id"])
            else:
                anonymous_refusal_sessions.add(event.get("session_id") or "none")
        elif kind == "caregiver_alerted" and event.get("kind") == "screen_refusal":
            if event.get("decision_id"):
                refusals.add(event["decision_id"])
            screen_alert_sessions.add(event.get("session_id") or "none")
        elif kind == "scam_checked" and event.get("verdict") == "scam":
            scams += 1
            rows.append({"guard": "ask", "amount": _money(event.get("amount")), "pattern": event.get("pattern"),
                         "check_id": event.get("check_id"), "seq": event.get("seq")})
    # A station refusal with no decision id is the same stop as the screen's alert in that session.
    refused = len(refusals) + len(anonymous_refusal_sessions - screen_alert_sessions)
    dollars = round(sum(row["amount"] for row in rows), 2)
    return {"dollars": f"{dollars:.2f}", "scams_stopped": scams + refused, "card_declines": card_declines,
            "rows": rows}
