import pytest

from policy.mandate import DEFAULT_MANDATE
from policy.store import save_caregiver_credential
from policy.verify_mandate import verify_mandate_assertion


def test_self_signed_mandate_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISIONS_PATH", str(tmp_path / "decisions.json"))
    monkeypatch.setenv("CAREGIVER_CREDENTIAL_PATH", str(tmp_path / "caregiver.json"))
    save_caregiver_credential({"credential_id": "priya", "public_key": "AQID", "sign_count": 0})
    mandate = {
        **DEFAULT_MANDATE,
        "passkey": {"credential_id": "someone_else", "public_key": "AQID", "response": {"id": "someone_else"}},
    }
    with pytest.raises(ValueError, match="pinned caregiver"):
        verify_mandate_assertion(mandate)


def test_rules_signed_on_the_new_form_keep_a_store_switched_off():
    from policy.mandate import DEFAULT_MANDATE, fill_v2

    signed = {**DEFAULT_MANDATE, "allowed_merchants": ["corner_market", "parkside_pharmacy"]}
    assert fill_v2(signed)["allowed_merchants"] == ["corner_market", "parkside_pharmacy"]
    # a file signed before the new form (no card rules) still shows the new stores
    old = {k: v for k, v in DEFAULT_MANDATE.items() if k != "card"} | {"allowed_merchants": ["corner_market"]}
    assert set(fill_v2(old)["allowed_merchants"]) == set(DEFAULT_MANDATE["allowed_merchants"])
