"""Visa Transaction Controls probe: the $480 drugstore swipe against a $60 threshold.

Needs a Visa Developer project with VTC (developer.visa.com → Dashboard → Create project):
    keys/visa/cert.pem, keys/visa/key.pem     two-way SSL ("Generate a CSR for me"; the key is offered once)
    VISA_VDP_USER_ID, VISA_VDP_PASSWORD       Credentials tab
    VISA_VTC_PAN                              Test Data: the PAN prefix + 0001 (e.g. 4514170000000001)
keys/ is gitignored; never commit them.

    python -m merchant.vtc_probe             # helloworld, enroll the PAN, set rules, ask for a $480 decision

If the project's MLE toggle is on, Customer Rules may still take plain
JSON in sandbox; if it answers an encryption error, write that down.
"""

import datetime
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE = "https://sandbox.api.visa.com"
CERT, KEY = ROOT / "keys" / "visa" / "cert.pem", ROOT / "keys" / "visa" / "key.pem"
# VTC has no pharmacy, gift-card or utility category; mirror what it has.
RULES = {
    "globalControls": [{"isControlEnabled": True, "shouldDeclineAll": False, "declineThreshold": 60}],
    "transactionControls": [{"controlType": "TCT_ATM_WITHDRAW", "isControlEnabled": True, "declineThreshold": 100},
                            {"controlType": "TCT_E_COMMERCE", "isControlEnabled": True, "declineThreshold": 150}],
    "merchantControls": [{"controlType": "MCT_GAMBLING", "isControlEnabled": True, "shouldDeclineAll": True},
                         {"controlType": "MCT_HOUSEHOLD", "isControlEnabled": True, "declineThreshold": 200}],
}


def missing() -> list[str]:
    gone = [p.name for p in (CERT, KEY) if not p.exists()]
    return gone + [v for v in ("VISA_VDP_USER_ID", "VISA_VDP_PASSWORD", "VISA_VTC_PAN") if not os.environ.get(v)]


def session() -> requests.Session:
    if missing():
        raise RuntimeError(f"missing: {', '.join(missing())} (see merchant/vtc_probe.py)")
    s = requests.Session()
    s.cert = (str(CERT), str(KEY))
    s.auth = (os.environ["VISA_VDP_USER_ID"], os.environ["VISA_VDP_PASSWORD"])
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    return s


def decision_request(pan: str, amount: float = 480, mcc: str = "5912", store: str = "Five Points Drug",
                     reference: int | None = None) -> dict:
    """A card-present swipe for /vctc/validation/v1/decisions. reference makes the retrieval and transaction ids."""
    ref = reference if reference is not None else int(round(amount * 100))
    return {"primaryAccountNumber": pan, "cardholderBillAmount": amount, "decisionType": "RECOMMENDED",
            "messageType": "0100", "processingCode": "000000", "retrievalReferenceNumber": f"{ref % 10**12:012d}",
            "transactionID": f"{ref % 10**15:015d}",
            "dateTimeLocal": datetime.datetime.now(datetime.timezone.utc).strftime("%m%d%H%M%S"),
            "merchantInfo": {"name": store[:25], "merchantCategoryCode": str(mcc), "countryCode": "USA",
                             "currencyCode": "840", "transactionAmount": amount, "city": "Atlanta", "region": "GA",
                             "postalCode": "30303"},
            # Required: a card-present swipe at a store's attended terminal (enum values from the API's 400s).
            "pointOfServiceInfo": {"securityLevelIndicator": "000", "terminalType": "POS_TERMINAL",
                                   "presentationData": {"isCardPresent": True, "howPresented": "CUSTOMER_PRESENT"},
                                   "terminalClass": {"isAttended": True, "howOperated": "CUSTOMER_OPERATED",
                                                     "deviceLocation": "ON_PREMISE"}}}


def step(name: str, response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = response.text[:300]
    print(f"{name}: HTTP {response.status_code} · correlation {response.headers.get('X-CORRELATION-ID')}")
    print(f"    {json.dumps(body)[:500]}")
    return body if isinstance(body, dict) else {}


def main():
    try:
        s, pan = session(), os.environ["VISA_VTC_PAN"]
    except RuntimeError as e:
        raise SystemExit(str(e)) from e
    step("helloworld", s.get(f"{BASE}/vdp/helloworld", timeout=20))
    enrolled = step("enroll PAN", s.post(f"{BASE}/vctc/customerrules/v1/consumertransactioncontrols",
                                         json={"primaryAccountNumber": pan}, timeout=20))
    doc = (enrolled.get("resource") or {}).get("documentID")
    if not doc:
        raise SystemExit("no documentID: stop here and write the status down")
    step("set rules", s.put(f"{BASE}/vctc/customerrules/v1/consumertransactioncontrols/{doc}/rules", json=RULES,
                            timeout=20))
    for amount, expected in ((480, True), (20, False)):
        decided = step(f"decision ${amount} at Five Points Drug",
                       s.post(f"{BASE}/vctc/validation/v1/decisions", json=decision_request(pan, amount), timeout=20))
        answer = (decided.get("resource") or {}).get("decisionResponse") or {}
        print(f"    -> shouldDecline={answer.get('shouldDecline')} (expected {expected}), "
              f"rule {answer.get('declineRuleCategory')}\n")


if __name__ == "__main__":
    main()
