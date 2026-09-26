"""Ledger events from the merchant, in the contracts/events.schema.json shape.

Every event is kept in memory for the wall's merchant panel (/panel) and posted fire-and-forget to
{RELAY_URL}/events when RELAY_URL is set. A slow or down relay never delays an order.
"""

import asyncio
import collections
import os
import time

import httpx

RECENT: collections.deque = collections.deque(maxlen=200)
_pending: set[asyncio.Task] = set()  # keep forward tasks alive until they finish


def record(type_: str, *, session_id: str | None = None, mandate_id: str | None = None, **fields) -> dict:
    return {
        "type": type_,
        "session_id": session_id or "none",
        "mandate_id": mandate_id or "none",
        "t": int(time.time() * 1000),
        "source": "merchant",
        **fields,
    }


async def _forward(relay: str, event: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            await client.post(f"{relay.rstrip('/')}/events", json=event)
    except httpx.HTTPError as e:
        print(f"[ledger] relay forward failed: {e}")


async def emit(type_: str, **fields) -> dict:
    event = record(type_, **fields)
    RECENT.append(event)
    print(f"[ledger] {type_} {fields}")
    relay = os.environ.get("RELAY_URL")
    if relay:
        task = asyncio.create_task(_forward(relay, event))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    return event


def clear() -> None:
    RECENT.clear()
