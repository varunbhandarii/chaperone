"""Visa risk score per agent order: Cybersource Decision Manager (POST /risk/v1/decisions) on the store's own
sandbox account, after the order is made and off its path (policy gives the whole signed order 5 s).

The order carries no card yet (Ruth pays on the hosted page), so the score is for the sandbox test card and
the order's amount; the answer is ACCEPTED / REJECTED / PENDING_REVIEW with riskInformation.score.result.
Budget RISK_BUDGET_S (2 s); a slow or failed call records the error and never touches the order's status.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time

from common import merchants
from merchant.cybs_rest import Creds, signed_request
from merchant.visa_probes import DM_BODY

RISK_BUDGET_S = 2.0


def request_body(order: dict) -> dict:
    body = copy.deepcopy(DM_BODY)
    body["clientReferenceInformation"]["code"] = order["order_id"]
    body["orderInformation"]["amountDetails"]["totalAmount"] = order["amount"]
    body["orderInformation"]["lineItems"] = [
        {"productSKU": line["sku"], "productName": line["name"][:60], "quantity": line["qty"],
         "unitPrice": line["unit_price"]} for line in order["lines"]]
    return body


def read_answer(status_code: int, body) -> dict:
    info = body if isinstance(body, dict) else {}
    score = ((info.get("riskInformation") or {}).get("score") or {}).get("result")
    reason = (info.get("errorInformation") or {}).get("reason") or info.get("reason")
    if status_code < 300 and info.get("status"):
        return {"status": info["status"], "score": score, "id": info.get("id"), "error": None}
    return {"status": None, "score": None, "id": info.get("id"), "error": reason or f"HTTP {status_code}"}


async def score(order: dict) -> dict:
    """{status, score, id, error, ms, account}; never raises."""
    entry = merchants.get(order.get("merchant")) or merchants.get(merchants.DEFAULT)
    merchant_id, key_id, secret, _ = merchants.credentials(entry)
    started = time.perf_counter()
    if not (merchant_id and key_id and secret):
        return {"status": None, "score": None, "id": None, "error": "no Cybersource account", "ms": 0,
                "account": None}
    creds = Creds(merchant_id, key_id, secret)
    try:
        r = await asyncio.wait_for(asyncio.to_thread(
            signed_request, creds, "POST", "/risk/v1/decisions", json.dumps(request_body(order)), RISK_BUDGET_S),
            RISK_BUDGET_S + 0.2)
        try:
            body = r.json()
        except ValueError:
            body = r.text[:200]
        out = read_answer(r.status_code, body)
    except Exception as e:  # noqa: BLE001 - a timeout or network error is recorded, never raised into the order
        out = {"status": None, "score": None, "id": None, "error": f"{type(e).__name__}: {e}"[:200]}
    return {**out, "ms": round((time.perf_counter() - started) * 1000), "account": merchant_id}
