import time

import pytest
from fastapi.testclient import TestClient

from relay import ledger, vtc


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeVisa:
    def __init__(self, decline=True, fail=False):
        self.calls, self.decline, self.fail = [], decline, fail

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url.split("/v1/")[-1], json))
        if self.fail:
            raise ConnectionError("visa down")
        if url.endswith("consumertransactioncontrols"):
            return Reply(201, {"resource": {"documentID": "ctc-doc-1"}})
        return Reply(200, {"resource": {"decisionResponse": {"shouldDecline": self.decline,
                                                             "declineRuleCategory": "PCT_GLOBAL" if self.decline else None}}})

    def put(self, url, json=None, timeout=None):
        self.calls.append(("PUT", url.split("/v1/")[-1], json))
        return Reply(200, {"resource": json})


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("VISA_VTC_PAN", "4514170000000001")
    monkeypatch.setattr(vtc, "_state", {"document_id": None, "rules": None})
    monkeypatch.setattr(vtc, "_policy_card", lambda: {"default_cap": 60, "atm_daily_cap": 100})


SWIPE = {"type": "card_decision", "session_id": "none", "mandate_id": "m", "seq": 7, "token": "tok_480",
         "store": "Five Points Drug", "mcc": "5912", "amount": 480.0, "result": "declined"}


def test_rules_mirror_the_mandate_card():
    rules = vtc.rules_from({"default_cap": 60, "atm_daily_cap": 100})
    assert rules["globalControls"][0]["declineThreshold"] == 60
    atm = next(c for c in rules["transactionControls"] if c["controlType"] == "TCT_ATM_WITHDRAW")
    assert atm["declineThreshold"] == 100
    assert rules["merchantControls"] == [{"controlType": "MCT_GAMBLING", "isControlEnabled": True, "shouldDeclineAll": True}]


def test_first_swipe_mirrors_the_rules_then_asks_visa():
    visa, posted = FakeVisa(), []
    vtc.handle(SWIPE, posted.append, session_factory=lambda: visa)
    assert [c[:2] for c in visa.calls] == [("POST", "consumertransactioncontrols"),
                                           ("PUT", "consumertransactioncontrols/ctc-doc-1/rules"),
                                           ("POST", "decisions")]
    decision = visa.calls[-1][2]
    assert decision["cardholderBillAmount"] == 480.0 and decision["merchantInfo"]["merchantCategoryCode"] == "5912"
    [event] = posted
    assert event["type"] == "vtc_decision" and event["token"] == "tok_480" and event["should_decline"] is True
    assert event["rule"] == "PCT_GLOBAL" and event["error"] is None
    assert list(ledger.VALIDATOR.iter_errors(event)) == []
    vtc.handle({**SWIPE, "token": "tok_2"}, posted.append, session_factory=lambda: visa)
    assert [c[:2] for c in visa.calls[3:]] == [("POST", "decisions")]  # rules already mirrored


def test_mandate_signed_re_mirrors_without_a_decision():
    visa, posted = FakeVisa(), []
    vtc.handle({"type": "mandate_signed", "session_id": "none", "mandate_id": "m"}, posted.append,
               session_factory=lambda: visa)
    assert posted == [] and visa.calls[-1][0] == "PUT"


def test_a_visa_failure_is_posted_never_raised():
    posted = []
    vtc.handle(SWIPE, posted.append, session_factory=lambda: FakeVisa(fail=True))
    assert posted[0]["should_decline"] is None and "visa down" in posted[0]["error"]
    assert list(ledger.VALIDATOR.iter_errors(posted[0])) == []


def test_relay_schedules_vtc_after_storing_a_card_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER", ledger.Ledger(tmp_path / "live.jsonl"))
    monkeypatch.setattr(vtc, "enabled", lambda: True)
    visa = FakeVisa()
    monkeypatch.setattr(vtc.vtc_probe, "session", lambda: visa)
    client = TestClient(ledger.app)
    event = {**SWIPE, "t": 1, "source": "policy", "reason_key": "card_cooldown", "card_last4": "1111"}
    event.pop("seq")
    assert client.post("/events", json=event).status_code == 202
    for _ in range(100):
        types = [e["type"] for e in ledger.LEDGER.read_live()]
        if "vtc_decision" in types:
            break
        time.sleep(0.02)
    assert types == ["card_decision", "vtc_decision"]


def test_off_without_keys(monkeypatch):
    monkeypatch.setattr(vtc.vtc_probe, "missing", lambda: ["cert.pem"])
    assert not vtc.enabled()
    monkeypatch.setattr(vtc.vtc_probe, "missing", lambda: [])
    monkeypatch.setenv("VTC_MIRROR", "0")
    assert not vtc.enabled()
