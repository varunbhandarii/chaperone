from merchant import visa_probes


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body, self.headers, self.text = status, body, {"v-c-correlation-id": "c1"}, str(body)

    def json(self):
        return self._body


def test_icc_statuses_are_read_as_documented():
    assert visa_probes.icc_meaning(404, {"response": {"rmsg": "Resource not found"}}) == "not routed"
    assert visa_probes.icc_meaning(401, {}).startswith("exists")
    assert visa_probes.icc_meaning(403, {}) == "not enabled on our merchant"
    assert visa_probes.icc_meaning(400, {"reason": "MISSING_FIELD", "message": "missing fields"}).startswith("past")
    assert visa_probes.icc_meaning(400, {"response": {"rmsg": "Bad Request"}}).startswith("routed; gateway")


def test_decision_manager_answers(monkeypatch):
    creds = visa_probes.Creds("m", "k", "c2VjcmV0")
    monkeypatch.setattr(visa_probes, "signed_request", lambda *a, **k: Reply(201, {
        "id": "790", "status": "ACCEPTED", "riskInformation": {"score": {"result": "32"}}}))
    row = visa_probes.probe_dm(creds)
    assert row["meaning"] == "works: ACCEPTED, score 32" and row["id"] == "790"
    monkeypatch.setattr(visa_probes, "signed_request", lambda *a, **k: Reply(400, {
        "status": "INVALID_REQUEST", "errorInformation": {"reason": "INVALID_MERCHANT_CONFIGURATION"}}))
    assert visa_probes.probe_dm(creds)["meaning"].startswith("not enabled")
