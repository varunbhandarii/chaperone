import json

import jsonschema

from common import merchants
from relay import ledger


def test_registry_lists_every_store_and_terminal():
    ids = [m["id"] for m in merchants.all_merchants()]
    assert ids == ["corner_market", "parkside_pharmacy", "main_street_home", "peachtree_power", "quickgift_cards"]
    assert [m["id"] for m in merchants.storefronts()] == ids[:4]
    assert merchants.get("quickgift_cards")["kind"] == "blocked"
    assert merchants.name("parkside_pharmacy") == "Parkside Pharmacy"
    assert merchants.get("nope") is None
    stores = {s["acceptor_id"]: s["mcc"] for s in merchants.registry()["card_terminal_stores"]}
    assert stores["GIFTCARDMALL1"] == "6540" and stores["FIVEPTSDRUG01"] == "5912"


def test_credentials_fall_back_to_the_main_account(monkeypatch):
    for k in ("MERCHANT_ID", "API_KEY_ID", "SECRET_KEY"):
        monkeypatch.setenv("VISA_ACCEPTANCE_" + k, "main_" + k)
        monkeypatch.delenv("CYBS_PARKSIDE_" + k, raising=False)
    monkeypatch.setenv("CYBS_MAINST_MERCHANT_ID", "only_one_of_three")
    assert merchants.credentials(merchants.get("corner_market")) == ("main_MERCHANT_ID", "main_API_KEY_ID", "main_SECRET_KEY", True)
    assert merchants.credentials(merchants.get("parkside_pharmacy"))[3] is False
    assert merchants.credentials(merchants.get("main_street_home"))[0] == "main_MERCHANT_ID"
    for k in ("MERCHANT_ID", "API_KEY_ID", "SECRET_KEY"):
        monkeypatch.setenv("CYBS_PARKSIDE_" + k, "pk_" + k)
    assert merchants.credentials(merchants.get("parkside_pharmacy")) == ("pk_MERCHANT_ID", "pk_API_KEY_ID", "pk_SECRET_KEY", True)


def _event(**kw):
    return {"session_id": "none", "mandate_id": "m", "t": 1, **kw}


def test_new_event_types_validate():
    ok = [
        _event(type="scam_checked", source="policy", check_id="sc_1", verdict="scam", pattern="utility", sources=[], ms=900, channel="station"),
        _event(type="caution", source="policy", rule_ids=["S3"], action="slow", words="urgent"),
        _event(type="risk_changed", source="policy", cooldown_until="2026-09-27T18:05:00Z", reason="scam call", source_event="sc_1"),
        _event(type="card_decision", source="policy", card_last4="1111", store="Five Points Drug", mcc="5912", amount=480,
               result="declined", reason_key="card_cooldown", reason="cool-down", cooldown=True, network="VISA", provider="lithic"),
        _event(type="card_hold_released", source="policy", hold_id="h1", store="Five Points Drug", max_amount=45, allowed_until=1),
        _event(type="bill_checked", source="merchant", biller="Peachtree Power", balance_due="86.40", due_date="2026-10-15", past_due=False),
        _event(type="line_call", source="line", call_id="c1", phase="ended", from_last4="0100", seconds=42),
    ]
    for event in ok:
        assert list(ledger.VALIDATOR.iter_errors(event)) == [], event["type"]
    bad = _event(type="card_decision", source="policy", result="maybe")
    assert list(ledger.VALIDATOR.iter_errors(bad))
