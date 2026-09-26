"""RFC 9421 verifier plus the digest check and the 8-minute nonce store."""

from __future__ import annotations

import datetime

import requests
from http_message_signatures import HTTPMessageVerifier
from http_message_signatures.exceptions import InvalidSignature

from common.config import KEY_ID
from signer.keys import jwks_lookup
from signer.sign import WINDOW, KeyResolver, content_digest


def _algorithms():
    from http_message_signatures import algorithms

    return algorithms


class ReplayError(InvalidSignature):
    pass


class DigestMismatch(InvalidSignature):
    pass


class NonceStore:
    def __init__(self):
        self._seen: dict[str, datetime.datetime] = {}

    def consume(self, nonce: str, expires_at: datetime.datetime) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
        if nonce in self._seen:
            raise ReplayError("rejected: replay")
        self._seen[nonce] = expires_at


def prepared_from_parts(method: str, url: str, headers, body: bytes) -> requests.PreparedRequest:
    return requests.Request(method, url, headers=dict(headers), data=body).prepare()


def verify_request(request: requests.PreparedRequest, nonce_store: NonceStore, *, public_key=None):
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
        raise InvalidSignature("signature window exceeds 8 minutes")
    if str(params.get("keyid")) != KEY_ID:
        raise InvalidSignature("unknown keyid")
    nonce = str(params["nonce"])
    expires_at = datetime.datetime.fromtimestamp(expires, datetime.timezone.utc)
    nonce_store.consume(nonce, expires_at)
    return results[0]
