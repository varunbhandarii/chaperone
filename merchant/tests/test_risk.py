import asyncio
import os
import secrets

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from common.host_header import HEADERS as HOST  # noqa: E402
from merchant import orders, risk, visa  # noqa: E402
from merchant.orders import app  # noqa: E402

ORDER = {"order_id": "ord_1", "merchant": "parkside_pharmacy", "amount": "8.00",
         "lines": [{"sku": "RX-001", "name": "Lisinopril 10 mg", "qty": 1, "unit_price": "8.00"}]}


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def test_request_carries_the_order(monkeypatch):
    body = risk.request_body(ORDER)
    assert body["clientReferenceInformation"]["code"] == "ord_1"
    assert body["orderInformation"]["amountDetails"]["totalAmount"] == "8.00"
    assert body["orderInformation"]["lineItems"][0]["productSKU"] == "RX-001"
    assert risk.DM_BODY["clientReferenceInformation"]["code"] == "chaperone-dm-001"  # the template is untouched


def test_score_uses_the_stores_own_account(monkeypatch):
    for k in ("MERCHANT_ID", "API_KEY_ID", "SECRET_KEY"):
        monkeypatch.setenv("CYBS_PARKSIDE_" + k, "pk_" + k)
    seen = {}

    def fake(creds, method, path, body, timeout):
        seen.update(merchant=creds.merchant_id, path=path, timeout=timeout)
        return Reply(201, {"id": "790", "status": "ACCEPTED", "riskInformation": {"score": {"result": "32"}}})
    monkeypatch.setattr(risk, "signed_request", fake)
    out = asyncio.run(risk.score(ORDER))
    assert out["status"] == "ACCEPTED" and out["score"] == "32" and out["account"] == "pk_M…T_ID"
    assert seen == {"merchant": "pk_MERCHANT_ID", "path": "/risk/v1/decisions", "timeout": risk.RISK_BUDGET_S}


def test_errors_and_slowness_are_recorded_not_raised(monkeypatch):
    monkeypatch.setenv("VISA_ACCEPTANCE_MERCHANT_ID", "m")
    monkeypatch.setenv("VISA_ACCEPTANCE_API_KEY_ID", "k")
    monkeypatch.setenv("VISA_ACCEPTANCE_SECRET_KEY", "c2VjcmV0")
    corner = {**ORDER, "merchant": "corner_market"}
    monkeypatch.setattr(risk, "signed_request", lambda *a, **k: Reply(400, {
        "errorInformation": {"reason": "INVALID_MERCHANT_CONFIGURATION"}}))
    assert asyncio.run(risk.score(corner))["error"] == "INVALID_MERCHANT_CONFIGURATION"

    def slow(*a, **k):
        import time
        time.sleep(3)
    monkeypatch.setattr(risk, "RISK_BUDGET_S", 0.1)
    monkeypatch.setattr(risk, "signed_request", slow)
    out = asyncio.run(risk.score(corner))
    assert out["status"] is None and out["error"].startswith("TimeoutError") and out["ms"] < 1000


def test_real_links_get_a_score_on_the_order_and_the_ledger(monkeypatch):
    async def fake_score(order):
        return {"status": "ACCEPTED", "score": "32", "id": "790", "error": None, "ms": 300, "account": "acct"}
    monkeypatch.setattr(risk, "score", fake_score)
    mock = visa.MockPaymentLinks("http://testserver")

    class RealLooking:  # the mock page, labelled as a real Visa link
        backend = "visa"

        async def create(self, *a, **k):
            link = await mock.create(*a, **k)
            link.backend = "visa"
            return link
    monkeypatch.setattr(orders.storefront_links, "links", lambda merchant=None: RealLooking())
    with TestClient(app, headers=HOST) as client:
        client.post("/reset")
        body = {"mandate_id": "m", "decision_id": "d_" + secrets.token_hex(4), "session_id": "s1",
                "cart": {"merchant": "parkside_pharmacy", "items": [{"sku": "RX-001", "qty": 1}]}}
        placed = client.post("/orders", json=body).json()
        assert placed["risk"] == {"status": "pending"}
        for _ in range(50):
            got = client.get(f"/orders/{placed['order_id']}").json()
            if got["risk"].get("status") != "pending":
                break
            asyncio.run(asyncio.sleep(0.02))
        assert got["risk"]["status"] == "ACCEPTED" and got["risk"]["score"] == "32"
        scored = [e for e in client.get("/panel").json()["events"] if e["type"] == "risk_scored"]
        assert scored and scored[-1]["merchant"] == "parkside_pharmacy" and scored[-1]["score"] == "32"


def test_mock_links_get_no_score():
    with TestClient(app, headers=HOST) as client:
        body = {"mandate_id": "m", "decision_id": "d_" + secrets.token_hex(4), "session_id": "s1",
                "cart": {"items": [{"sku": "BAK-001", "qty": 1}]}}
        assert client.post("/orders", json=body).json()["risk"] is None
    assert orders.risk is risk
