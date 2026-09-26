"""Ruth's risk state: the cool-down a scam story puts on her card.

The Ask guard writes it (/scam-check); the Card guard reads it on every swipe.

    set_cooldown(mandate_id, hours, reason, source_event) -> state
    load_risk(mandate_id) -> {mandate_id, cooldown_until, reason, source_event, active}
    GET  /risk?mandate_id=          for the UIs
    POST /risk/clear {mandate_id}   caregiver only (x-chaperone-marker for action "risk")

Stored in sessions/risk.json (RISK_PATH) and cleared on reset. Posts `risk_changed`.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from common.config import decisions_path, env
from policy.events import post_event

DEFAULT_MANDATE_ID = "m_ruth_2026_09"
_lock = threading.RLock()


def risk_path() -> Path:
    return Path(env("RISK_PATH", str(decisions_path().parent / "risk.json")))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read() -> dict:
    path = risk_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8") or "{}") or {}


def _write(data: dict) -> None:
    path = risk_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _state(mandate_id: str, entry: dict | None) -> dict:
    until = (entry or {}).get("cooldown_until")
    active = bool(until) and datetime.fromisoformat(until) > _now()
    return {
        "mandate_id": mandate_id,
        "cooldown_until": until if active else None,
        "reason": (entry or {}).get("reason") if active else None,
        "source_event": (entry or {}).get("source_event") if active else None,
        "active": active,
    }


def load_risk(mandate_id: str | None = None) -> dict:
    mandate_id = mandate_id or DEFAULT_MANDATE_ID
    with _lock:
        return _state(mandate_id, _read().get(mandate_id))


def set_cooldown(mandate_id: str | None, hours: float, reason: str, source_event: str | None = None,
                 session_id: str | None = None) -> dict:
    """Start or extend the cool-down; a later end time never shortens an earlier one."""
    mandate_id = mandate_id or DEFAULT_MANDATE_ID
    until = (_now() + timedelta(hours=hours)).replace(microsecond=0)
    with _lock:
        data = _read()
        current = _state(mandate_id, data.get(mandate_id))
        if current["active"] and datetime.fromisoformat(current["cooldown_until"]) >= until:
            return current
        data[mandate_id] = {"cooldown_until": until.isoformat(), "reason": reason, "source_event": source_event,
                            "set_at": _now().isoformat()}
        _write(data)
        state = _state(mandate_id, data[mandate_id])
    post_event("risk_changed", session_id or "none", mandate_id, cooldown_until=state["cooldown_until"],
               reason=reason, source_event=source_event)
    return state


def clear(mandate_id: str | None, by: str = "caregiver") -> dict:
    mandate_id = mandate_id or DEFAULT_MANDATE_ID
    with _lock:
        data = _read()
        data.pop(mandate_id, None)
        _write(data)
    post_event("risk_changed", "none", mandate_id, cooldown_until=None, reason=f"cleared by {by}", source_event=None)
    return load_risk(mandate_id)


def reset() -> None:
    with _lock:
        if risk_path().exists():
            risk_path().unlink()


router = APIRouter()


@router.get("/risk")
def get_risk(mandate_id: str = ""):
    return load_risk(mandate_id or None)


@router.post("/risk/clear")
def post_clear(request: Request, body: dict | None = None):
    from policy.approvals import marker_matches

    if not marker_matches("mandate", request.headers.get("x-chaperone-marker", ""), "risk"):
        raise HTTPException(401, "sign in required")
    return clear((body or {}).get("mandate_id"))
