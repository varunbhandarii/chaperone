"""Cybersource webhook signature (v-c-signature) and payment matching.

Header: v-c-signature: t=<ms since epoch>;keyId=<uuid>;sig=<base64>
    sig = base64(HMAC-SHA256(base64decode(key), f"{t}.{message}"))
The message is the raw request body, as the Cybersource webhook docs specify. Cybersource's own plugins
(commercetools AuthenticationHelper.authenticateNetToken, Salesforce B2C WebhookNotification.js) sign only
JSON.stringify(body["payload"]) instead, which is json.dumps(separators=(",", ":"), ensure_ascii=False), so
that variant is accepted too. Whatever the variant, only the part the signature covers is trusted
(Verified.signed): with the payload variant the envelope, eventType included, is attacker-controlled.

Keys: CYBS_WEBHOOK_KEY_ID / CYBS_WEBHOOK_KEY (base64), from Cybersource's key service when the webhook is
registered, or any local key for merchant.simulate_payment; it can pay any store's order. Each store's own
account may add <prefix>WEBHOOK_KEY_ID / <prefix>WEBHOOK_KEY (its cybs_env prefix in contracts/merchants.json,
e.g. CYBS_PARKSIDE_WEBHOOK_KEY); the keyId picks the key, and an account's key only pays the orders of the stores
that account serves (Verified.stores): its own store, and any store that falls back to it for lack of its own
credentials (the main account, VISA_ACCEPTANCE_, serves every such store).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

TOLERANCE_MS = 60 * 60 * 1000  # Cybersource's Salesforce plugin accepts one hour
_seen_signatures: dict[str, int] = {}  # sig -> t, pruned past the tolerance


class WebhookError(Exception):
    pass


@dataclass
class Verified:
    body: dict
    t: int
    key_id: str
    variant: str  # "raw_body" or "payload"
    duplicate: bool
    signed: dict  # the part the signature covers: the whole body, or {"payload": body["payload"]}
    merchant: str | None = None  # the store whose own key signed it; None for the shared key
    stores: tuple[str, ...] | None = None  # the stores that key's account serves; None for the shared key


def parse_signature_header(value: str) -> tuple[int, str, str]:
    parts = {}
    for piece in value.strip().strip('"').split(";"):
        if "=" in piece:
            k, v = piece.split("=", 1)  # base64 may end in "=", so split once
            parts[k.strip()] = v.strip()
    try:
        return int(parts["t"]), parts["keyId"], parts["sig"]
    except (KeyError, ValueError) as exc:
        raise WebhookError("malformed v-c-signature") from exc


def compact(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _digest(key_b64: str, t: int, message: bytes) -> bytes:
    return hmac.new(base64.b64decode(key_b64), f"{t}.".encode() + message, hashlib.sha256).digest()


def sign(key_b64: str, t: int, message: str | bytes) -> str:
    message = message.encode() if isinstance(message, str) else message
    return base64.b64encode(_digest(key_b64, t, message)).decode()


def configured_key() -> tuple[str, str]:
    key_id, key = os.environ.get("CYBS_WEBHOOK_KEY_ID", ""), os.environ.get("CYBS_WEBHOOK_KEY", "")
    if not key_id or not key:
        raise WebhookError("webhook key not configured (CYBS_WEBHOOK_KEY_ID, CYBS_WEBHOOK_KEY)")
    return key_id, key


def _account_prefix(entry: dict) -> str:
    """The env prefix of the Cybersource account a store's links are made on (its own, or the main one)."""
    from common import merchants

    return entry.get("cybs_env") if merchants.credentials(entry)[3] else merchants.MAIN_PREFIX


def configured_keys() -> dict[str, tuple[str, str | None, tuple[str, ...] | None]]:
    """keyId -> (key, the store it belongs to, the stores its account serves); (key, None, None) for the shared key."""
    from common import merchants

    keys: dict[str, tuple[str, str | None, tuple[str, ...] | None]] = {}
    shared_id, shared = os.environ.get("CYBS_WEBHOOK_KEY_ID", ""), os.environ.get("CYBS_WEBHOOK_KEY", "")
    if shared_id and shared:
        keys[shared_id] = (shared, None, None)
    fronts = merchants.storefronts()
    for entry in fronts:
        prefix = entry.get("cybs_env")
        key_id, key = os.environ.get(f"{prefix}WEBHOOK_KEY_ID", ""), os.environ.get(f"{prefix}WEBHOOK_KEY", "")
        if prefix and key_id and key:
            served = [entry["id"]] + [e["id"] for e in fronts if e["id"] != entry["id"] and _account_prefix(e) == prefix]
            keys[key_id] = (key, entry["id"], tuple(served))
    if not keys:
        raise WebhookError("webhook key not configured (CYBS_WEBHOOK_KEY_ID, CYBS_WEBHOOK_KEY)")
    return keys


def verify(raw_body: bytes, header: str | None, now_ms: int | None = None) -> Verified:
    if not header:
        raise WebhookError("missing v-c-signature")
    keys = configured_keys()
    t, got_key_id, sig = parse_signature_header(header)
    # compare_digest on bytes: a str compare raises on non-ASCII
    match = next((kid for kid in keys if hmac.compare_digest(got_key_id.encode(), kid.encode())), None)
    if match is None:
        raise WebhookError("unknown keyId")
    key, store, served = keys[match]
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(now_ms - t) > TOLERANCE_MS:
        raise WebhookError("stale timestamp")
    try:
        body = json.loads(raw_body)
    except (ValueError, RecursionError) as exc:  # deeply nested JSON from the public URL must be a 401, not a 500
        raise WebhookError("body is not JSON") from exc
    if not isinstance(body, dict):
        raise WebhookError("body is not a JSON object")
    try:
        got = base64.b64decode(sig, validate=True)
    except ValueError as exc:
        raise WebhookError("sig is not base64") from exc
    candidates = [("raw_body", raw_body, body)]
    if "payload" in body:
        candidates.append(("payload", compact(body["payload"]).encode(), {"payload": body["payload"]}))
    for variant, message, signed in candidates:
        if hmac.compare_digest(_digest(key, t, message), got):
            for old_sig, old_t in list(_seen_signatures.items()):
                if now_ms - old_t > TOLERANCE_MS:
                    del _seen_signatures[old_sig]
            duplicate = sig in _seen_signatures
            _seen_signatures[sig] = t
            return Verified(body=body, t=t, key_id=got_key_id, variant=variant, duplicate=duplicate, signed=signed,
                            merchant=store, stores=served)
    raise WebhookError("signature mismatch")


def strings_in(tree) -> list[str]:
    """Every string anywhere in the notification; the purchase number's exact field is undocumented."""
    out, stack = [], [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            out.append(node)
    return out


PAID_STATUSES = {"PAID", "COMPLETED", "AUTHORIZED", "CAPTURED", "SETTLED", "TRANSMITTED"}


def is_payment(signed: dict) -> tuple[bool, str]:
    """Whether the signed part says a payment happened, and what it said. Never reads the unsigned envelope."""
    event_type = str(signed.get("eventType") or "")
    if event_type:
        return "payment" in event_type.lower(), event_type
    statuses = set()
    stack = [signed.get("payload")]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("eventType"), str):
                return "payment" in node["eventType"].lower(), node["eventType"]
            if isinstance(node.get("status"), str):
                statuses.add(node["status"].upper())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    paid = sorted(statuses & PAID_STATUSES)
    return (True, f"status {paid[0]}") if paid else (False, "no payment in the signed payload")


def headers_for(body: bytes | str, key_id: str, key_b64: str, t: int | None = None,
                variant: str = "raw_body") -> dict[str, str]:
    """Headers Cybersource sends. Used by merchant.simulate_payment and tests."""
    t = t if t is not None else int(time.time() * 1000)
    raw = body.encode() if isinstance(body, str) else body
    parsed = json.loads(raw)
    sig = sign(key_b64, t, raw if variant == "raw_body" else compact(parsed["payload"]))
    return {
        "Content-Type": "application/json",
        "v-c-signature": f"t={t};keyId={key_id};sig={sig}",
        "v-c-event-type": parsed.get("eventType", ""),
        "v-c-organization-id": parsed.get("organizationId", ""),
        "v-c-product-name": parsed.get("productId", ""),
        "v-c-request-type": "NEW",
        "v-c-retry-count": str(parsed.get("retryNumber", 0)),
        "v-c-webhook-id": parsed.get("webhookId", ""),
    }
