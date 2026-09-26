"""Card decisions. A swipe is a lookup over the mandate, the cool-down and Ruth's history.

No model and no person run on this path. Lithic needs an answer in well under a second.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from policy.events import post_event
from policy.mandate import DEFAULT_MANDATE

_lock = threading.RLock()
_seen: dict[str, dict] = {}

BLOCKED_RESULTS = {
    "card_blocked_category": "UNAUTHORIZED_MERCHANT",
    "card_over_cap": "VELOCITY_EXCEEDED",
    "card_atm_cap": "VELOCITY_EXCEEDED",
    "card_cooldown": "SUSPECTED_FRAUD",
    "card_unusual_amount": "SUSPECTED_FRAUD",
}

REASONS = {
    "card_blocked_category": "This kind of store is blocked on Ruth's card.",
    "card_over_cap": "This is over the limit for this kind of store.",
    "card_atm_cap": "Cash withdrawals are over the limit on Ruth's card.",
    "card_cooldown": "Ruth's card is on a cool-down after a scam check.",
    "card_unusual_amount": "This is much more than Ruth usually spends at this kind of store.",
}

# Physical stores for the card demo, used when contracts/merchants.json is not on this branch yet.
TERMINAL_STORES = {
    "FIVEPTSDRUG01": {"name": "Five Points Drug", "mcc": "5912"},
    "CORNERMKT01": {"name": "Corner Market", "mcc": "5411"},
    "GIFTCARDMALL1": {"name": "GiftCard Kiosk", "mcc": "6540"},
    "COINATM0001": {"name": "Coin ATM", "mcc": "6051"},
}


def state_path() -> Path:
    configured = os.environ.get("CARD_STATE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "sessions" / "card_state.json"


def read_risk(mandate_id: str) -> dict:
    """Every swipe asks the scam check's cool-down. Missing policy.risk means there is no cool-down yet."""
    try:
        from policy.risk import load_risk
    except ImportError:
        return {}
    return load_risk(mandate_id) or {}


def _blank() -> dict:
    return {"decisions": [], "holds": {}, "passes": [], "history": []}


def load_state() -> dict:
    path = state_path()
    if not path.exists():
        return _blank()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blank()
    data.setdefault("decisions", [])
    data.setdefault("holds", {})
    data.setdefault("passes", [])
    data.setdefault("history", [])
    return data


def save_state(data: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def reset_state() -> None:
    with _lock:
        _seen.clear()
        path = state_path()
        if path.exists():
            path.unlink()


def swipe_dollars(payload: dict) -> float:
    holder = ((payload.get("amounts") or {}).get("cardholder") or {})
    cents = holder.get("amount")
    if cents is None:
        cents = payload.get("amount") or 0
    return int(cents) / 100


def median_dollars(history: list[dict], mcc: str) -> float:
    amounts = [float(row["amount"]) for row in history if str(row.get("mcc")) == str(mcc)]
    if not amounts:
        return 30.0
    return float(statistics.median(amounts))


def _cap(table: dict, mcc: str, default: float) -> float:
    if mcc in table:
        return float(table[mcc])
    return float(default)


def _cooldown_active(risk: dict, now: datetime) -> bool:
    """policy.risk.load_risk sets active. A dict with only cooldown_until still counts, for tests."""
    if not risk:
        return False
    if "active" in risk:
        return bool(risk["active"])
    until = risk.get("cooldown_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > now
    except ValueError:
        return False


def _matching_pass(passes: list[dict], card_token: str, acceptor_id: str, amount: float, now: datetime) -> dict | None:
    for entry in passes:
        if entry.get("used"):
            continue
        if entry.get("card_token") != card_token or entry.get("acceptor_id") != acceptor_id:
            continue
        if amount - float(entry.get("max_amount") or 0) > 0.001:
            continue
        try:
            if datetime.fromisoformat(entry["allowed_until"]) < now:
                continue
        except (KeyError, ValueError):
            continue
        return entry
    return None


def decide(payload: dict, mandate: dict, risk: dict | None = None, history: list | None = None,
           passes: list | None = None, now: datetime | None = None) -> dict:
    """Pure decision. `passes` is the list of Priyank's one-time allows; a match is marked used."""
    now = now or datetime.now(timezone.utc)
    token = str(payload.get("token") or "")
    status = payload.get("status") or "AUTHORIZATION"
    merchant = payload.get("merchant") or {}
    card = payload.get("card") or {}
    mcc = str(merchant.get("mcc") or "")
    amount = swipe_dollars(payload)
    rules = (mandate or DEFAULT_MANDATE).get("card") or DEFAULT_MANDATE["card"]
    answer = {
        "token": token,
        "result": "APPROVED",
        "reason_key": None,
        "reason": None,
        "mcc": mcc,
        "amount": amount,
        "store": merchant.get("descriptor") or merchant.get("acceptor_id") or "",
        "card_last4": card.get("last_four") or "",
        "hold_id": None,
        "consumed_pass": None,
    }
    if status == "BALANCE_INQUIRY" or status != "AUTHORIZATION":
        return answer

    found = _matching_pass(passes or [], card.get("token") or "", merchant.get("acceptor_id") or "", amount, now)
    if found:
        found["used"] = True
        answer["consumed_pass"] = found.get("hold_id")
        return answer

    blocked = {str(code) for code in (rules.get("blocked_mccs") or [])}
    if mcc in blocked:
        return _decline(answer, "card_blocked_category")

    if _cooldown_active(risk or {}, now):
        caps = (rules.get("cooldown") or {}).get("caps") or {}
        if mcc == "6011":
            return _decline(answer, "card_atm_cap")
        limit = _cap(caps, mcc, caps.get("default", 25))
        if amount > limit + 0.001:
            return _decline(answer, "card_cooldown")
    else:
        if mcc == "6011" and amount > float(rules.get("atm_daily_cap") or 0) + 0.001:
            return _decline(answer, "card_atm_cap")
        limit = _cap(rules.get("category_caps") or {}, mcc, rules.get("default_cap") or 60)
        if amount > limit + 0.001:
            return _decline(answer, "card_over_cap")

    usual = median_dollars(history or [], mcc)
    multiplier = float(rules.get("unusual_multiplier") or 3)
    if amount > usual * multiplier + 0.001:
        return _decline(answer, "card_unusual_amount")
    return answer


def _decline(answer: dict, reason_key: str) -> dict:
    answer["result"] = BLOCKED_RESULTS[reason_key]
    answer["reason_key"] = reason_key
    answer["reason"] = REASONS[reason_key]
    answer["hold_id"] = "h_" + (answer["token"] or "swipe")[:12]
    return answer


def terminal_store(acceptor_id: str) -> dict | None:
    path = Path(__file__).resolve().parents[1] / "contracts" / "merchants.json"
    if path.exists():
        try:
            registry = json.loads(path.read_text(encoding="utf-8"))
            for store in registry.get("card_terminal_stores") or []:
                if store.get("acceptor_id") == acceptor_id:
                    return store
        except (OSError, json.JSONDecodeError):
            pass
    return TERMINAL_STORES.get(acceptor_id)


def handle_authorization(payload: dict, mandate: dict | None = None) -> dict:
    """Decide once per Lithic token, store the hold on a decline, and remember approved swipes."""
    token = str(payload.get("token") or "")
    with _lock:
        if token and token in _seen:
            return _seen[token]
        state = load_state()
        active = mandate or DEFAULT_MANDATE
        mandate_id = active.get("mandate_id") or DEFAULT_MANDATE["mandate_id"]
        risk = read_risk(mandate_id)
        answer = decide(payload, active, risk, state["history"], state["passes"])
        record = {
            "token": token,
            "result": "approved" if answer["result"] == "APPROVED" else "declined",
            "lithic_result": answer["result"],
            "reason_key": answer["reason_key"],
            "reason": answer["reason"],
            "store": answer["store"],
            "mcc": answer["mcc"],
            "amount": answer["amount"],
            "card_last4": answer["card_last4"],
            "hold_id": answer["hold_id"],
            "at": datetime.now(timezone.utc).isoformat(),
        }
        state["decisions"].append(record)
        state["decisions"] = state["decisions"][-50:]
        if answer["result"] == "APPROVED" and (payload.get("status") or "AUTHORIZATION") == "AUTHORIZATION":
            state["history"].append({"mcc": answer["mcc"], "amount": answer["amount"]})
        elif answer["hold_id"]:
            card = payload.get("card") or {}
            merchant = payload.get("merchant") or {}
            state["holds"][answer["hold_id"]] = {
                "hold_id": answer["hold_id"],
                "token": token,
                "card_token": card.get("token") or "",
                "acceptor_id": merchant.get("acceptor_id") or "",
                "mcc": answer["mcc"],
                "store": answer["store"],
                "max_amount": answer["amount"],
                "card_last4": answer["card_last4"],
                "reason_key": answer["reason_key"],
                "released": False,
            }
        save_state(state)
        public = {"result": answer["result"], "token": token}
        if token:
            _seen[token] = public
        post_event(
            "card_decision", "none", mandate_id,
            card_last4=answer["card_last4"], store=answer["store"], mcc=answer["mcc"],
            amount=answer["amount"], result=record["result"], reason_key=answer["reason_key"],
            reason=answer["reason"], hold_id=answer["hold_id"],
            cooldown=bool(risk.get("active") or risk.get("cooldown_until")),
            network="visa", provider="lithic",
        )
        return public


def allow_hold(hold_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    with _lock:
        state = load_state()
        hold = state["holds"].get(hold_id)
        if not hold or hold.get("released"):
            raise KeyError(hold_id)
        until = now + timedelta(minutes=10)
        hold["released"] = True
        hold["allowed_until"] = until.isoformat()
        state["passes"].append({
            "hold_id": hold_id,
            "card_token": hold.get("card_token") or "",
            "acceptor_id": hold.get("acceptor_id") or "",
            "max_amount": hold.get("max_amount"),
            "allowed_until": until.isoformat(),
            "used": False,
        })
        save_state(state)
    post_event(
        "card_hold_released", "none", DEFAULT_MANDATE["mandate_id"],
        hold_id=hold_id, store=hold.get("store"), max_amount=hold.get("max_amount"),
        allowed_until=until.isoformat(),
    )
    return {"hold_id": hold_id, "store": hold.get("store"), "max_amount": hold.get("max_amount"),
            "allowed_until": until.isoformat()}


def public_state(mandate_id: str) -> dict:
    state = load_state()
    risk = read_risk(mandate_id)
    open_holds = [hold for hold in state["holds"].values() if not hold.get("released")]
    return {
        "decisions": list(reversed(state["decisions"][-20:])),
        "holds": open_holds,
        "cooldown_until": risk.get("cooldown_until"),
        "cooldown_reason": risk.get("reason"),
    }


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def verify_webhook(headers, body: bytes, secret: str, now: float | None = None) -> bool:
    """Lithic ASA signs with the Standard Webhooks headers. Reject anything older than five minutes."""
    import base64
    import hashlib
    import hmac

    wid = headers.get("webhook-id") or ""
    stamp = headers.get("webhook-timestamp") or ""
    signature = headers.get("webhook-signature") or ""
    if not wid or not stamp or not signature or not secret.startswith("whsec_"):
        return False
    try:
        sent = int(stamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent) > 300:
        return False
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{wid}.{stamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        hmac.compare_digest(expected, part.split(",", 1)[1])
        for part in signature.split()
        if "," in part
    )


def simulate_swipe(acceptor_id: str, amount_cents: int) -> dict:
    """Ask Lithic to authorize a swipe. The enrolled /card/asa endpoint makes the decision."""
    store = terminal_store(acceptor_id)
    if store is None:
        raise LookupError("unknown store")
    key = os.environ.get("LITHIC_API_KEY", "")
    card_path = Path(__file__).resolve().parents[1] / "sessions" / "card.json"
    if not key or not card_path.exists():
        raise RuntimeError("Ruth's card is not enrolled yet")
    card = json.loads(card_path.read_text(encoding="utf-8"))
    pan = card.get("pan") or ""
    if not pan:
        raise RuntimeError("Ruth's card is not enrolled yet")
    from lithic import Lithic

    client = Lithic(api_key=key, environment="sandbox")
    started = time.perf_counter()
    sim = client.transactions.simulate_authorization(
        amount=amount_cents,
        descriptor=store["name"].upper(),
        pan=pan,
        mcc=str(store["mcc"]),
        merchant_acceptor_id=acceptor_id,
    )
    txn = client.transactions.retrieve(sim.token)
    return {
        "token": sim.token,
        "result": txn.result,
        "status": txn.status,
        "ms": elapsed_ms(started),
        "store": store["name"],
        "card_last4": card.get("last_four") or "",
    }
