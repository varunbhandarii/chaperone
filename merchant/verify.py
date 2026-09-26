"""RFC 9421 verifier plus the digest check and the 8-minute nonce store. See contracts/signing.md.

verify_prepared() is the check itself, on a requests.PreparedRequest.
verify_request() is what the order flow calls: it rebuilds the request from the raw parts and
returns every check for the wall. MERCHANT_VERIFY=off (the default) records the check as skipped
instead of failing orders; enforce runs the verifier and rejects anything that fails.

Step 6 of the contract (enforce mode only): the body's decision_id must be known to policy
(GET {POLICY_URL}/decisions/{id}, 300 ms timeout) with decision "allow", or "approve" whose approval was
approved, and the decision's cart (sku and qty) must equal the order's cart, so a valid decision cannot be
replayed with a different cart. Policy unreachable fails closed. Accepted decision shapes, until the
policy contract pins one: cart under "cart" or "priced_cart", lines under "items" or "lines"; approval as
{"approval": {"approved": true}} or {"approval_status": "approved"}.
"""

from __future__ import annotations

import datetime
import inspect
import json
import os
import threading
from collections import Counter
from dataclasses import dataclass, field

import httpx
import requests
from http_message_signatures import HTTPMessageVerifier
from http_message_signatures.exceptions import InvalidSignature

from common.config import KEY_ID
from signer.keys import jwks_lookup
from signer.sign import WINDOW, KeyResolver, content_digest

# The library still accepts a signature a few seconds past expires (clock skew), so keep each
# nonce a little longer than that or a replay slips through in the gap.
NONCE_GRACE = datetime.timedelta(seconds=60)


def _algorithms():
    from http_message_signatures import algorithms

    return algorithms


class ReplayError(InvalidSignature):
    pass


class DigestMismatch(InvalidSignature):
    pass


class WindowError(InvalidSignature):
    pass


class DecisionError(InvalidSignature):
    pass


DECISION_TIMEOUT = 0.3


def _cart_counts(cart) -> Counter:
    if isinstance(cart, dict):
        cart = cart.get("items") or cart.get("lines") or []
    counts: Counter = Counter()
    for line in cart or []:
        counts[str(line["sku"])] += int(line.get("qty", 1))
    return counts


async def fetch_decision(decision_id: str) -> dict | None:
    """Every policy failure (down, slow, 5xx, not JSON) is a failed decision check, never a signature failure."""
    base = os.environ.get("POLICY_URL", "http://127.0.0.1:8001").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=DECISION_TIMEOUT) as client:
            r = await client.get(f"{base}/decisions/{decision_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as exc:
        raise DecisionError(f"policy answered {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise DecisionError(f"policy unreachable: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise DecisionError("policy answered with something other than JSON") from exc


async def check_decision(body: bytes, fetch=None, existing_order: dict | None = None) -> str:
    """Contract step 6. Returns the passing detail or raises DecisionError. fetch may be sync or async.

    existing_order is set for a post-purchase request (cancel, refund) on that order: the decision must be the
    order's own, or a policy decision made for that order (its order_id), and there is no cart to compare.
    """
    fetch = fetch or fetch_decision  # looked up at call time so tests can patch the module
    try:
        order = json.loads(body)
        decision_id = order["decision_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise DecisionError("body has no decision_id") from exc
    decision = fetch(decision_id)
    if inspect.isawaitable(decision):
        decision = await decision
    if decision is not None and not isinstance(decision, dict):
        raise DecisionError(f"policy sent an unreadable decision for {decision_id}")
    if decision is None:
        raise DecisionError(f"unknown decision {decision_id}")
    outcome = decision.get("decision")
    if outcome == "approve":
        approval = decision.get("approval") or {}
        if not (approval.get("approved") is True or decision.get("approval_status") == "approved"):
            raise DecisionError(f"decision {decision_id} awaits caregiver approval")
    elif outcome != "allow":
        raise DecisionError(f"decision {decision_id} is {outcome}")
    if existing_order is not None:
        order_id = existing_order.get("order_id")
        if decision_id != existing_order.get("decision_id") and decision.get("order_id") != order_id:
            raise DecisionError(f"decision {decision_id} is not for order {order_id}")
        if order.get("mandate_id") != existing_order.get("mandate_id"):
            raise DecisionError(f"order {order_id} belongs to another mandate")
        return f"{decision_id} is {outcome}, for order {order_id}"
    decided = _cart_counts(decision.get("cart") or decision.get("priced_cart"))
    if decided != _cart_counts(order.get("cart")):
        raise DecisionError(f"cart differs from decision {decision_id}")
    return f"{decision_id} is {outcome}, cart matches"


class NonceStore:
    def __init__(self):
        self._seen: dict[str, datetime.datetime] = {}
        self._lock = threading.Lock()

    def consume(self, nonce: str, expires_at: datetime.datetime) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        with self._lock:
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
            if nonce in self._seen:
                raise ReplayError("rejected: replay")
            self._seen[nonce] = expires_at + NONCE_GRACE


NONCES = NonceStore()


def prepared_from_parts(method: str, url: str, headers, body: bytes) -> requests.PreparedRequest:
    return requests.Request(method, url, headers=dict(headers), data=body).prepare()


def verify_prepared(request: requests.PreparedRequest, nonce_store: NonceStore, *, public_key=None):
    raw = request.body if isinstance(request.body, bytes) else (request.body or b"")
    if isinstance(raw, str):
        raw = raw.encode()
    expected = content_digest(raw)
    actual = request.headers.get("Content-Digest")
    if actual != expected:
        raise DigestMismatch("content digest mismatch")
    public_key = public_key or jwks_lookup(KEY_ID)
    results = HTTPMessageVerifier(
        signature_algorithm=_algorithms().ED25519,
        key_resolver=KeyResolver(public_key=public_key),
    ).verify(request, max_age=WINDOW, expect_tag="agent-payer-auth")
    params = dict(results[0].parameters)
    created = int(params["created"])
    expires = int(params["expires"])
    if expires - created > int(WINDOW.total_seconds()):
        raise WindowError("signature window exceeds 8 minutes")
    if str(params.get("keyid")) != KEY_ID:
        raise InvalidSignature("unknown keyid")
    nonce = str(params["nonce"])
    expires_at = datetime.datetime.fromtimestamp(expires, datetime.timezone.utc)
    nonce_store.consume(nonce, expires_at)
    return results[0]


@dataclass
class Verification:
    ok: bool
    keyid: str | None = None
    checks: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)  # nonce, created, expires of a verified signature (for the wall)


STEPS = ("content_digest", "signature", "window", "nonce", "decision")


async def verify_request(
    method: str,
    authority: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    *,
    public_key=None,
    nonce_store: NonceStore | None = None,
    fetch_decision=None,
    existing_order: dict | None = None,
) -> Verification:
    mode = os.environ.get("MERCHANT_VERIFY", "off")
    has_sig = "signature" in headers and "signature-input" in headers
    if mode == "off":
        detail = "verifier skipped (MERCHANT_VERIFY=off)"
        if has_sig:
            detail += "; signature headers present"
        return Verification(ok=True, checks=[{"id": "signature", "passed": None, "detail": detail}])
    if not has_sig:
        return Verification(ok=False, checks=[{"id": "signature", "passed": False, "detail": "unsigned request"}])

    request = prepared_from_parts(method, f"http://{authority}{path}", headers, body)
    try:
        result = verify_prepared(request, nonce_store or NONCES, public_key=public_key)
        decision_detail = await check_decision(body, fetch_decision, existing_order)
    except Exception as exc:  # every failure becomes a red check on the wall, never a 500
        if isinstance(exc, DigestMismatch):
            failed = "content_digest"
        elif isinstance(exc, WindowError):
            failed = "window"
        elif isinstance(exc, ReplayError):
            failed = "nonce"
        elif isinstance(exc, DecisionError):
            failed = "decision"
        else:
            failed = "signature"
        checks = []
        for step in STEPS:
            if step == failed:
                checks.append({"id": step, "passed": False, "detail": str(exc) or type(exc).__name__})
                break
            checks.append({"id": step, "passed": True, "detail": "ok"})
        return Verification(ok=False, checks=checks)

    params = dict(result.parameters)
    keyid = str(params.get("keyid"))
    span = int(params["expires"]) - int(params["created"])
    return Verification(
        ok=True,
        keyid=keyid,
        params={"nonce": str(params["nonce"]), "created": int(params["created"]), "expires": int(params["expires"])},
        checks=[
            {"id": "content_digest", "passed": True, "detail": "sha-256 matches the body"},
            {"id": "signature", "passed": True, "detail": f"ed25519, keyid {keyid}, tag agent-payer-auth"},
            {"id": "window", "passed": True, "detail": f"{span}s window, inside 8 minutes"},
            {"id": "nonce", "passed": True, "detail": "first use"},
            {"id": "decision", "passed": True, "detail": decision_detail},
        ],
    )
