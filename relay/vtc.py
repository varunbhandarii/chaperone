"""Visa Transaction Controls mirror: the same card rules on Ruth's VTC test PAN, and Visa's answer for
every swipe, next to our own.

  * Rules: on mandate_signed, and before the first swipe after the relay starts, Ruth's card rules go to VTC:
    the global per-swipe threshold from card.default_cap, ATM from card.atm_daily_cap, e-commerce, and
    gambling blocked. VTC has no pharmacy, gift-card or utility category.
  * Decisions: after each card_decision reaches the ledger, the same amount, MCC and store go to
    /vctc/validation/v1/decisions and the answer is posted as vtc_decision (with the swipe's token).

Everything runs in a worker thread after the event is stored: the swipe was already answered by policy, and a VTC
error is logged and posted with `error`, never raised. Off unless keys/visa/ and VISA_VDP_* / VISA_VTC_PAN exist
(merchant/vtc_probe.py). VTC_MIRROR=0 turns it off.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import zlib

import httpx

from merchant import vtc_probe

MIRRORED_TYPES = {"card_decision", "mandate_signed"}
_lock = threading.Lock()
_state: dict = {"document_id": None, "rules": None}
_tasks: set = set()


def enabled() -> bool:
    return os.environ.get("VTC_MIRROR", "1") != "0" and not vtc_probe.missing()


def rules_from(card: dict | None) -> dict:
    card = card or {}
    cap = float(card.get("default_cap") or 60)
    atm = float(card.get("atm_daily_cap") or 100)
    online = float(card.get("ecommerce_cap") or max(cap, 150))
    return {
        "globalControls": [{"isControlEnabled": True, "shouldDeclineAll": False, "declineThreshold": cap}],
        "transactionControls": [
            {"controlType": "TCT_ATM_WITHDRAW", "isControlEnabled": True, "declineThreshold": atm},
            {"controlType": "TCT_E_COMMERCE", "isControlEnabled": True, "declineThreshold": online}],
        "merchantControls": [{"controlType": "MCT_GAMBLING", "isControlEnabled": True, "shouldDeclineAll": True}],
    }


def _policy_card() -> dict | None:
    base = os.environ.get("POLICY_URL", "http://127.0.0.1:8001").rstrip("/")
    try:
        body = httpx.get(f"{base}/mandate", timeout=2).json()
    except (httpx.HTTPError, ValueError):
        return None
    mandate = body.get("mandate") if isinstance(body.get("mandate"), dict) else body
    return mandate.get("card") if isinstance(mandate, dict) else None


def mirror(session, card: dict | None) -> dict:
    """Enroll the PAN once and put the rules. Returns the rules sent."""
    pan = os.environ["VISA_VTC_PAN"]
    rules = rules_from(card)
    with _lock:
        if not _state["document_id"]:
            r = session.post(f"{vtc_probe.BASE}/vctc/customerrules/v1/consumertransactioncontrols",
                             json={"primaryAccountNumber": pan}, timeout=10)
            _state["document_id"] = ((r.json() or {}).get("resource") or {}).get("documentID")
            if not _state["document_id"]:
                raise RuntimeError(f"enroll answered HTTP {r.status_code}")
        r = session.put(f"{vtc_probe.BASE}/vctc/customerrules/v1/consumertransactioncontrols/"
                        f"{_state['document_id']}/rules", json=rules, timeout=10)
        if r.status_code >= 300:
            raise RuntimeError(f"rules answered HTTP {r.status_code}")
        _state["rules"] = rules
    return rules


def decide(session, event: dict) -> dict:
    """The vtc_decision payload for one card_decision."""
    started = time.perf_counter()
    token = event.get("token")
    reference = zlib.crc32(str(token or event.get("seq") or time.time()).encode())
    body = vtc_probe.decision_request(os.environ["VISA_VTC_PAN"], float(event.get("amount") or 0),
                                      str(event.get("mcc") or "5999"), str(event.get("store") or "Store"), reference)
    r = session.post(f"{vtc_probe.BASE}/vctc/validation/v1/decisions", json=body, timeout=10)
    answer = ((r.json() or {}).get("resource") or {}).get("decisionResponse") or {} if r.status_code < 300 else {}
    return {"token": token, "store": event.get("store"), "mcc": str(event.get("mcc") or ""),
            "amount": event.get("amount"), "should_decline": answer.get("shouldDecline"),
            "rule": answer.get("declineRuleCategory"), "ms": round((time.perf_counter() - started) * 1000),
            "error": None if r.status_code < 300 else f"HTTP {r.status_code}"}


def handle(event: dict, post, session_factory=vtc_probe.session) -> None:
    """Worker-thread body: mirror rules or ask VTC about a swipe, then post the answer. Never raises."""
    try:
        session = session_factory()
        if event["type"] == "mandate_signed" or _state["rules"] is None:
            mirror(session, _policy_card())
        if event["type"] == "card_decision":
            post({"type": "vtc_decision", "session_id": event.get("session_id") or "none",
                  "mandate_id": event.get("mandate_id") or "none", "t": int(time.time() * 1000), "source": "relay",
                  **decide(session, event)})
    except Exception as e:  # noqa: BLE001 - VTC is a mirror; a failure is logged and posted, never raised
        print(f"[vtc] {event.get('type')}: {type(e).__name__}: {e}", flush=True)
        if event.get("type") == "card_decision":
            post({"type": "vtc_decision", "session_id": event.get("session_id") or "none",
                  "mandate_id": event.get("mandate_id") or "none", "t": int(time.time() * 1000), "source": "relay",
                  "token": event.get("token"), "store": event.get("store"), "mcc": str(event.get("mcc") or ""),
                  "amount": event.get("amount"), "should_decline": None, "rule": None,
                  "error": f"{type(e).__name__}: {e}"[:200]})


def after_event(event: dict, post) -> None:
    """Called by the relay after it stores an event. Schedules the VTC work and returns at once."""
    if event.get("type") not in MIRRORED_TYPES or event.get("replay") or not enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        threading.Thread(target=handle, args=(event, post), daemon=True).start()
        return
    task = loop.create_task(asyncio.to_thread(handle, event, post))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
