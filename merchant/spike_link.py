"""Payment link smoke check: create a payment link, print it, open it.

    python -m merchant.spike_link            # demo cart ($11.49) through the running merchant on :8002
    python -m merchant.spike_link --direct   # $1.00 link straight through the Visa MCP (checks sandbox creds)
"""

import argparse
import asyncio
import json
import os
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEMO_ORDER = {
    "mandate_id": "mandate-ruth-001",
    "decision_id": "dec-spike-001",
    "session_id": "spike",
    "approval_id": None,
    "cart": {"items": [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 1}]},
}


async def direct():
    from merchant.visa import CRED_VARS, LineItem, VisaMcpPaymentLinks, new_purchase_number

    missing = [v for v in CRED_VARS if not os.environ.get(v)]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)} in .env")
    links = VisaMcpPaymentLinks(*(os.environ[v] for v in CRED_VARS))
    try:
        link = await links.create(new_purchase_number(), "1.00", "USD",
                                  [LineItem(productName="Chaperone sandbox test", quantity=1, unitPrice="1.00")])
    finally:
        await links.close()
    print(json.dumps(link.raw, indent=2)[:1500])
    return link.url


def via_merchant(base: str):
    r = httpx.post(f"{base}/orders", json=DEMO_ORDER, timeout=120)
    r.raise_for_status()
    order = r.json()
    print(json.dumps({k: order[k] for k in ("order_id", "amount", "status", "payment_link")}, indent=2))
    return order["payment_link"]["url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--direct", action="store_true", help="call the Visa MCP directly with a $1.00 link")
    ap.add_argument("--merchant", default="http://127.0.0.1:8002")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    url = asyncio.run(direct()) if args.direct else via_merchant(args.merchant)
    print(f"\nPAYMENT LINK: {url}")
    if not args.no_open:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
