"""Visa probes on our own sandbox account only.

    python -m merchant.visa_probes            # Intelligent Commerce path probe + Decision Manager
    python -m merchant.visa_probes --json     # the same, as JSON

1. Intelligent Commerce: POST {} to /icc/v1/instructions and /acp/v1/instructions. A working call needs JWT
   auth, message-level encryption and pilot IDs, so only the status is read:
   404 path not routed · 401 or MLE error: exists, needs JWT/MLE · 403 product not on our merchant ·
   400 with missing fields: past the auth gate · a bare 400 "Bad Request": routed, turned away at the gateway.
2. Decision Manager: POST /risk/v1/decisions with the sandbox test card. ACCEPTED / REJECTED / PENDING_REVIEW
   with riskInformation.score.result means it works; INVALID_MERCHANT_CONFIGURATION means it isn't set up.

Never uses the public testrest credentials.
"""

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from merchant.cybs_rest import Creds, signed_request  # noqa: E402

ICC_PATHS = ("/icc/v1/instructions", "/acp/v1/instructions")
DM_BODY = {
    "clientReferenceInformation": {"code": "chaperone-dm-001"},
    "paymentInformation": {"card": {"number": "4111111111111111", "expirationMonth": "12", "expirationYear": "2031"}},
    "orderInformation": {
        "amountDetails": {"currency": "USD", "totalAmount": "11.49"},
        "billTo": {"firstName": "Ruth", "lastName": "Test", "address1": "1 Peachtree St", "locality": "Atlanta",
                   "administrativeArea": "GA", "postalCode": "30303", "country": "US", "email": "ruth@example.com",
                   "phoneNumber": "4045550100"},
    },
}


def creds() -> Creds:
    values = [os.environ.get(f"VISA_ACCEPTANCE_{k}", "") for k in ("MERCHANT_ID", "API_KEY_ID", "SECRET_KEY")]
    if not all(values):
        raise SystemExit("VISA_ACCEPTANCE_MERCHANT_ID / API_KEY_ID / SECRET_KEY missing in .env")
    return Creds(*values)


def _body(response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text[:300]


def icc_meaning(status: int, body) -> str:
    text = json.dumps(body).lower() if not isinstance(body, str) else body.lower()
    if status == 404:
        return "not routed"
    if status == 401 or "encrypt" in text or "mle" in text or "jwt" in text:
        return "exists; needs JWT and message-level encryption"
    if status == 403:
        return "not enabled on our merchant"
    if status == 400 and "missing" in text:
        return "past the auth gate (missing fields)"
    if status == 400:
        # A made-up path answers 404 "Resource not found", and /pts answers MISSING_FIELD, so this is a routed
        # path that the gateway turns away before any field check: no JWT and no encrypted payload.
        return "routed; gateway rejects it before field checks (needs JWT + message-level encryption)"
    return "see body"


def probe_icc(c: Creds) -> list[dict]:
    rows = []
    for path in ICC_PATHS:
        started = time.monotonic()
        r = signed_request(c, "POST", path, "{}", timeout=15)
        body = _body(r)
        rows.append({"probe": f"Intelligent Commerce {path}", "status": r.status_code,
                     "meaning": icc_meaning(r.status_code, body), "correlation_id": r.headers.get("v-c-correlation-id"),
                     "ms": round((time.monotonic() - started) * 1000), "body": body})
    return rows


def probe_dm(c: Creds) -> dict:
    started = time.monotonic()
    r = signed_request(c, "POST", "/risk/v1/decisions", json.dumps(DM_BODY), timeout=20)
    body = _body(r)
    info = body if isinstance(body, dict) else {}
    reason = (info.get("errorInformation") or {}).get("reason") or info.get("reason")
    score = ((info.get("riskInformation") or {}).get("score") or {}).get("result")
    if reason == "INVALID_MERCHANT_CONFIGURATION":
        meaning = "not enabled (INVALID_MERCHANT_CONFIGURATION): drop"
    elif info.get("status") in ("ACCEPTED", "REJECTED", "PENDING_REVIEW", "PENDING_AUTHENTICATION"):
        meaning = f"works: {info['status']}, score {score}"
    else:
        meaning = f"HTTP {r.status_code}{', ' + reason if reason else ''}"
    return {"probe": "Decision Manager /risk/v1/decisions", "status": r.status_code, "meaning": meaning,
            "decision": info.get("status"), "score": score, "id": info.get("id"),
            "correlation_id": r.headers.get("v-c-correlation-id"), "ms": round((time.monotonic() - started) * 1000),
            "body": body}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    c = creds()
    rows = probe_icc(c) + [probe_dm(c)]
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"merchant {c.merchant_id} on {c.host}")
    for row in rows:
        extra = f" · id {row['id']}" if row.get("id") else ""
        print(f"{row['probe']}: HTTP {row['status']} · {row['meaning']}{extra} · correlation {row['correlation_id']}"
              f" · {row['ms']} ms")
        print(f"    {json.dumps(row['body'])[:400]}")


if __name__ == "__main__":
    main()
