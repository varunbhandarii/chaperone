import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from policy.approvals import action_marker
from policy.card import decide, reset_state, verify_webhook
from policy.mandate import DEFAULT_MANDATE, visa_view
from policy.tests.test_checkout import client

NOW = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)
SECRET = "whsec_" + base64.b64encode(b"k" * 24).decode()


def swipe(mcc, dollars, acceptor="CORNERMKT01", token="tok_1", status="AUTHORIZATION", card_token="card_1"):
    return {
        "token": token,
        "status": status,
        "amounts": {"cardholder": {"amount": int(round(dollars * 100))}},
        "merchant": {"mcc": mcc, "descriptor": "STORE", "acceptor_id": acceptor},
        "card": {"token": card_token, "last_four": "4242"},
    }


def test_blocked_mcc_is_unauthorized_merchant():
    answer = decide(swipe("6540", 50, "GIFTCARDMALL1"), DEFAULT_MANDATE, now=NOW)
    assert answer["result"] == "UNAUTHORIZED_MERCHANT"
    assert answer["reason_key"] == "card_blocked_category"


def test_over_the_cap():
    answer = decide(swipe("5411", 160), DEFAULT_MANDATE, now=NOW)
    assert answer["result"] == "VELOCITY_EXCEEDED"
    assert answer["reason_key"] == "card_over_cap"


def test_unusual_amount_uses_the_history_median():
    history = [{"mcc": "5411", "amount": 20}] * 5
    answer = decide(swipe("5411", 80), DEFAULT_MANDATE, history=history, now=NOW)
    assert answer["reason_key"] == "card_unusual_amount"
    assert answer["result"] == "SUSPECTED_FRAUD"


def test_grocery_under_the_cap_is_approved():
    answer = decide(swipe("5411", 20), DEFAULT_MANDATE, now=NOW)
    assert answer["result"] == "APPROVED"
    assert answer["reason_key"] is None


def test_risk_active_flag_uses_the_cooldown_caps():
    cooled = decide(swipe("5912", 45, "FIVEPTSDRUG01"), DEFAULT_MANDATE, risk={"active": True, "reason": "scam"}, now=NOW)
    assert cooled["reason_key"] == "card_cooldown"
    cleared = decide(swipe("5912", 45, "FIVEPTSDRUG01"), DEFAULT_MANDATE, risk={"active": False, "cooldown_until": (NOW + timedelta(hours=1)).isoformat()}, now=NOW)
    assert cleared["result"] == "APPROVED"


def test_cooldown_declines_a_pharmacy_swipe_that_normally_passes():
    risk = {"cooldown_until": (NOW + timedelta(hours=1)).isoformat(), "reason": "scam"}
    cooled = decide(swipe("5912", 45, "FIVEPTSDRUG01"), DEFAULT_MANDATE, risk=risk, now=NOW)
    assert cooled["reason_key"] == "card_cooldown"
    open_card = decide(swipe("5912", 45, "FIVEPTSDRUG01"), DEFAULT_MANDATE, now=NOW)
    assert open_card["result"] == "APPROVED"


def test_allow_once_passes_exactly_one_retry():
    passes = [{
        "hold_id": "h_1", "card_token": "card_1", "acceptor_id": "CORNERMKT01",
        "max_amount": 160, "allowed_until": (NOW + timedelta(minutes=10)).isoformat(), "used": False,
    }]
    first = decide(swipe("5411", 160, token="retry"), DEFAULT_MANDATE, passes=passes, now=NOW)
    assert first["result"] == "APPROVED" and first["consumed_pass"] == "h_1"
    second = decide(swipe("5411", 160, token="retry2"), DEFAULT_MANDATE, passes=passes, now=NOW)
    assert second["result"] == "VELOCITY_EXCEEDED" and second["reason_key"] == "card_over_cap"


def test_a_thousand_row_history_decides_within_300_ms():
    import time

    history = [{"mcc": "5411", "amount": 12.5}] * 1000
    started = time.perf_counter()
    decide(swipe("5411", 20), DEFAULT_MANDATE, history=history, now=NOW)
    assert (time.perf_counter() - started) < 0.3


def test_webhook_rejects_a_stale_or_forged_signature():
    body = b'{"token":"t"}'
    stamp = str(int(NOW.timestamp()))
    signed = f"msg_1.{stamp}.".encode() + body
    good = base64.b64encode(hmac.new(base64.b64decode(SECRET.removeprefix("whsec_")), signed, hashlib.sha256).digest()).decode()
    headers = {"webhook-id": "msg_1", "webhook-timestamp": stamp, "webhook-signature": f"v1,{good}"}
    assert verify_webhook(headers, body, SECRET, now=NOW.timestamp())
    forged = dict(headers)
    forged["webhook-signature"] = "v1,bm90LXRoZS1zaWduYXR1cmU"
    assert not verify_webhook(forged, body, SECRET, now=NOW.timestamp())
    stale = dict(headers)
    stale["webhook-timestamp"] = str(int(NOW.timestamp()) - 600)
    assert not verify_webhook(stale, body, SECRET, now=NOW.timestamp())


def test_asa_is_idempotent_and_a_proxy_cannot_list_card_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LITHIC_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("CARD_STATE_PATH", str(tmp_path / "card_state.json"))
    reset_state()
    api = client(tmp_path, monkeypatch)
    body = json.dumps(swipe("5411", 20, token="tok_once")).encode()
    stamp = str(int(datetime.now(timezone.utc).timestamp()))
    signed = f"msg_2.{stamp}.".encode() + body
    good = base64.b64encode(hmac.new(base64.b64decode(SECRET.removeprefix("whsec_")), signed, hashlib.sha256).digest()).decode()
    headers = {"webhook-id": "msg_2", "webhook-timestamp": stamp, "webhook-signature": f"v1,{good}", "content-type": "application/json"}
    first = api.post("/card/asa", content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["result"] == "APPROVED"
    assert int(first.headers["x-chaperone-ms"]) < 300
    second = api.post("/card/asa", content=body, headers=headers)
    assert second.json() == first.json()
    blocked = json.dumps(swipe("6540", 50, "GIFTCARDMALL1", token="tok_gift")).encode()
    stamp = str(int(datetime.now(timezone.utc).timestamp()))
    signed = f"msg_3.{stamp}.".encode() + blocked
    good = base64.b64encode(hmac.new(base64.b64decode(SECRET.removeprefix("whsec_")), signed, hashlib.sha256).digest()).decode()
    gift = api.post("/card/asa", content=blocked, headers={**headers, "webhook-id": "msg_3", "webhook-timestamp": stamp, "webhook-signature": f"v1,{good}"})
    assert gift.json()["result"] == "UNAUTHORIZED_MERCHANT"
    assert api.get("/card/state", headers={"x-forwarded-for": "8.8.8.8"}).status_code == 403
    hold_id = json.loads((tmp_path / "card_state.json").read_text())["holds"]
    hold_id = next(iter(hold_id))
    allowed = api.post(f"/card/holds/{hold_id}/allow", headers={"X-Chaperone-Marker": action_marker(hold_id, "card")})
    assert allowed.status_code == 200, allowed.text
    assert api.post(f"/card/holds/{hold_id}/allow", headers={"X-Chaperone-Marker": action_marker(hold_id, "card")}).status_code == 404


def test_visa_view_matches_the_intelligent_commerce_shape():
    view = visa_view(DEFAULT_MANDATE)
    assert view["consumerPrompt"].startswith("Ruth's groceries")
    ids = [row["mandateId"] for row in view["mandates"]]
    assert "m_ruth_2026_09-corner_market" in ids
    grocery = next(row for row in view["mandates"] if row["merchantCategoryCode"] == "5411")
    assert grocery["declineThreshold"] == {"amount": "150.00", "currencyCode": "USD"}
    assert grocery["effectiveUntilTime"].isdigit()
    assert all(len(row["mandateId"]) <= 50 for row in view["mandates"])
