"""Ledger events from the merchant.

Kept in memory for the wall's merchant panel and forwarded best-effort to the relay when RELAY_URL
is set. The relay's ingest path (POST {RELAY_URL}/ledger/events) is a guess until the relay
contract lands; a failed forward never blocks an order.
"""

import collections
import os
import time

import httpx

RECENT: collections.deque = collections.deque(maxlen=200)


async def emit(event: str, **fields) -> dict:
    record = {"event": event, "t": time.time(), "source": "merchant", **fields}
    RECENT.append(record)
    print(f"[ledger] {event} {fields}")
    relay = os.environ.get("RELAY_URL")
    if relay:
        try:
            async with httpx.AsyncClient(timeout=0.5) as client:
                await client.post(f"{relay.rstrip('/')}/ledger/events", json=record)
        except httpx.HTTPError as e:
            print(f"[ledger] relay forward failed: {e}")
    return record
