"""RFC 9421 verifier plus the digest check and the 8-minute nonce store. See contracts/signing.md.

verify_prepared() is the check itself, on a requests.PreparedRequest.
verify_request() is what the order flow calls: it rebuilds the request from the raw parts and
returns every check for the wall. MERCHANT_VERIFY=off (the default) records the check as skipped
instead of failing orders; enforce runs the verifier and rejects anything that fails.

Not here yet: step 6 of the contract (decision_id is allow/approved on the ledger).
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field

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


class NonceStore:
    def __init__(self):
        self._seen: dict[str, datetime.datetime] = {}

    def consume(self, nonce: str, expires_at: datetime.datetime) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
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


STEPS = ("content_digest", "signature", "window", "nonce")


def verify_request(
    method: str,
    authority: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    *,
    public_key=None,
    nonce_store: NonceStore | None = None,
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
    except Exception as exc:  # every failure becomes a red check on the wall, never a 500
        if isinstance(exc, DigestMismatch):
            failed = "content_digest"
        elif isinstance(exc, WindowError):
            failed = "window"
        elif isinstance(exc, ReplayError):
            failed = "nonce"
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
        checks=[
            {"id": "content_digest", "passed": True, "detail": "sha-256 matches the body"},
            {"id": "signature", "passed": True, "detail": f"ed25519, keyid {keyid}, tag agent-payer-auth"},
            {"id": "window", "passed": True, "detail": f"{span}s window, inside 8 minutes"},
            {"id": "nonce", "passed": True, "detail": "first use"},
        ],
    )
