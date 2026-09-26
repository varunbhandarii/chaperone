"""Cybersource webhook signature (v-c-signature) and payment matching.

Header: v-c-signature: t=<ms since epoch>;keyId=<uuid>;sig=<base64>
Signature, as Cybersource's own plugins compute it (commercetools AuthenticationHelper.authenticateNetToken,
Salesforce B2C WebhookNotification.js):
    sig = base64(HMAC-SHA256(base64decode(key), f"{t}.{JSON.stringify(body['payload'])}"))
JSON.stringify is compact with keys in order, which is json.dumps(separators=(",", ":"), ensure_ascii=False).
We also accept f"{t}.{raw_body}" because Pay by Link's exact variant is undocumented; both need the key.

Key: CYBS_WEBHOOK_KEY_ID / CYBS_WEBHOOK_KEY (base64), from Cybersource's key service when the webhook is
registered, or any local key for merchant.simulate_payment.
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
    variant: str  # "payload" (official) or "raw_body"
    duplicate: bool


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


def sign(key_b64: str, t: int, message: str) -> str:
    digest = hmac.new(base64.b64decode(key_b64), f"{t}.{message}".encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def configured_key() -> tuple[str, str]:
    key_id, key = os.environ.get("CYBS_WEBHOOK_KEY_ID", ""), os.environ.get("CYBS_WEBHOOK_KEY", "")
    if not key_id or not key:
        raise WebhookError("webhook key not configured (CYBS_WEBHOOK_KEY_ID, CYBS_WEBHOOK_KEY)")
    return key_id, key


def verify(raw_body: bytes, header: str | None, now_ms: int | None = None) -> Verified:
    if not header:
        raise WebhookError("missing v-c-signature")
    key_id, key = configured_key()
    t, got_key_id, sig = parse_signature_header(header)
    if not hmac.compare_digest(got_key_id, key_id):
        raise WebhookError("unknown keyId")
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(now_ms - t) > TOLERANCE_MS:
        raise WebhookError("stale timestamp")
    try:
        body = json.loads(raw_body)
    except ValueError as exc:
        raise WebhookError("body is not JSON") from exc
    candidates = [("raw_body", raw_body.decode("utf-8", "replace"))]
    if isinstance(body, dict) and "payload" in body:
        candidates.insert(0, ("payload", compact(body["payload"])))
    for variant, message in candidates:
        if hmac.compare_digest(sign(key, t, message), sig):
            for old_sig, old_t in list(_seen_signatures.items()):
                if now_ms - old_t > TOLERANCE_MS:
                    del _seen_signatures[old_sig]
            duplicate = sig in _seen_signatures
            _seen_signatures[sig] = t
            return Verified(body=body, t=t, key_id=got_key_id, variant=variant, duplicate=duplicate)
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


def headers_for(body: bytes | str, key_id: str, key_b64: str, t: int | None = None) -> dict[str, str]:
    """Headers Cybersource sends, signed the official way. Used by merchant.simulate_payment and tests."""
    t = t if t is not None else int(time.time() * 1000)
    parsed = json.loads(body)
    sig = sign(key_b64, t, compact(parsed["payload"]))
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
