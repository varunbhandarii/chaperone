"""The Host's "the sandbox confirmed payment" button: a Cybersource Pay by Link payment notification for an
order, signed with CYBS_WEBHOOK_KEY the way Cybersource signs, posted to the merchant's webhook.

    python -m merchant.simulate_payment <order_id> [--merchant http://127.0.0.1:8002]

The Host says: "marked paid in the sandbox flow".
"""

import argparse
import datetime
import json
import os
import sys
import uuid

import httpx

from common.config import load_dotenv  # noqa: F401 - loads the repo-root .env on import
from merchant.webhooks import headers_for


def envelope(order: dict) -> dict:
    link = order["payment_link"]
    return {
        "eventType": "payByLink.merchant.payment",
        "webhookId": str(uuid.uuid4()),
        "productId": "payByLink",
        "organizationId": os.environ.get("VISA_ACCEPTANCE_MERCHANT_ID", "sandbox"),
        "eventDate": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "transactionTraceId": str(uuid.uuid4()),
        "retryNumber": 0,
        "payload": [{
            "data": {
                "id": link["id"],
                "purchaseInformation": {"purchaseNumber": link["purchase_number"]},
                "orderInformation": {"amountDetails": {"totalAmount": order["amount"], "currency": order["currency"]}},
                "status": "PAID",
            },
            "organizationId": os.environ.get("VISA_ACCEPTANCE_MERCHANT_ID", "sandbox"),
        }],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("order_id")
    ap.add_argument("--merchant", default=os.environ.get("MERCHANT_URL", "http://127.0.0.1:8002"))
    args = ap.parse_args()
    key_id, key = os.environ.get("CYBS_WEBHOOK_KEY_ID"), os.environ.get("CYBS_WEBHOOK_KEY")
    if not key_id or not key:
        print("Set CYBS_WEBHOOK_KEY_ID and CYBS_WEBHOOK_KEY in .env (see .env.example).", file=sys.stderr)
        return 2
    base = args.merchant.rstrip("/")
    r = httpx.get(f"{base}/orders/{args.order_id}", timeout=5)
    if r.status_code != 200:
        print(f"order {args.order_id}: HTTP {r.status_code} {r.text}", file=sys.stderr)
        return 1
    body = json.dumps(envelope(r.json()))
    r = httpx.post(f"{base}/webhooks/cybersource", content=body, headers=headers_for(body, key_id, key), timeout=5)
    print(f"HTTP {r.status_code} {r.text}")
    return 0 if r.status_code == 200 and r.json().get("status") == "paid" else 1


if __name__ == "__main__":
    sys.exit(main())
