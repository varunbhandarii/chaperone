"""Fire-and-forget ledger posts. A missing relay does not fail checkout."""

from __future__ import annotations

import time

import httpx

from common.config import env


def post_event(event_type: str, session_id: str, mandate_id: str, **fields) -> None:
    event = {
        "type": event_type,
        "session_id": session_id or "none",
        "mandate_id": mandate_id or "none",
        "t": int(time.time() * 1000),
        "source": "policy",
        **fields,
    }
    relay = env("RELAY_URL")
    if not relay:
        return
    try:
        httpx.post(f"{relay.rstrip('/')}/events", json=event, timeout=0.3)
    except httpx.HTTPError:
        return
