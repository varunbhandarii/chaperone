"""Cybersource sandbox diagnostics over REST (HTTP Signature, same creds as the Visa MCP).

    python -m merchant.cybs_check              # $1.00 test-card authorization -> AUTHORIZED or the exact reason
    python -m merchant.cybs_check <request_id> # why a transaction (e.g. from Transaction Management) failed
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from email.utils import formatdate
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

HOST = "apitest.cybersource.com"


def signed_request(method: str, path: str, body: str | None = None) -> httpx.Response:
    mid = os.environ["VISA_ACCEPTANCE_MERCHANT_ID"]
    kid = os.environ["VISA_ACCEPTANCE_API_KEY_ID"]
    secret = base64.b64decode(os.environ["VISA_ACCEPTANCE_SECRET_KEY"])
    headers = {"host": HOST, "date": formatdate(usegmt=True), "v-c-merchant-id": mid}
    signed = ["host", "date", "request-target"]
    if body is not None:
        headers["digest"] = "SHA-256=" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        headers["content-type"] = "application/json"
        signed.append("digest")
    signed.append("v-c-merchant-id")
    lines = [f"request-target: {method.lower()} {path}" if h == "request-target" else f"{h}: {headers[h]}" for h in signed]
    sig = base64.b64encode(hmac.new(secret, "\n".join(lines).encode(), hashlib.sha256).digest()).decode()
    headers["signature"] = (f'keyid="{kid}", algorithm="HmacSHA256", headers="{" ".join(signed)}", '
                            f'signature="{sig}"')
    return httpx.request(method, f"https://{HOST}{path}", headers=headers, content=body, timeout=30)


def explain(request_id: str, wait: float = 0) -> None:
    deadline = time.time() + wait
    while True:  # details take a few seconds to become searchable after a new transaction
        d = signed_request("GET", f"/tss/v2/transactions/{request_id}").json()
        if d.get("applicationInformation") or time.time() >= deadline:
            break
        time.sleep(3)
    processor = ((d.get("processorInformation") or {}).get("processor") or {}).get("name")
    print(f"transaction {request_id}  processor={processor}")
    for app in (d.get("applicationInformation") or {}).get("applications", []):
        if app.get("reasonCode") or app.get("rMessage"):
            print(f"  {app['name']:14} reason={app.get('reasonCode')} {app.get('rFlag', '')} {app.get('rMessage', '')}")


def auth_test() -> None:
    body = json.dumps({
        "clientReferenceInformation": {"code": "chaperone-auth-test"},
        "processingInformation": {"capture": False},
        "paymentInformation": {"card": {"number": "4111111111111111", "expirationMonth": "12",
                                        "expirationYear": "2030", "securityCode": "123"}},
        "orderInformation": {
            "amountDetails": {"totalAmount": "1.00", "currency": "USD"},
            "billTo": {"firstName": "Test", "lastName": "Shopper", "address1": "1 Test St", "locality": "Atlanta",
                       "administrativeArea": "GA", "postalCode": "30308", "country": "US",
                       "email": "test@example.com"},
        },
    })
    d = signed_request("POST", "/pts/v2/payments", body).json()
    print(f"$1.00 test authorization: {d.get('status')}  request_id={d.get('id')}")
    if d.get("status") != "AUTHORIZED" and d.get("id"):
        explain(d["id"], wait=30)


if __name__ == "__main__":
    explain(sys.argv[1]) if len(sys.argv) > 1 else auth_test()
