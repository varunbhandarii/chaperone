"""RFC 9421 Ed25519 signer. See contracts/signing.md."""

from __future__ import annotations

import datetime
import hashlib
import secrets

import requests
from http_message_signatures import HTTPMessageSigner, HTTPSignatureKeyResolver, algorithms
from http_sf.compat import Dictionary

from common.config import KEY_ID
from signer.keys import load_or_create_private_key

COVERED = ("@method", "@authority", "@path", "content-digest", "content-type")
WINDOW = datetime.timedelta(minutes=8)


def content_digest(body: bytes) -> str:
    return str(Dictionary({"sha-256": hashlib.sha256(body).digest()}))


class KeyResolver(HTTPSignatureKeyResolver):
    def __init__(self, private_key=None, public_key=None):
        self.private_key = private_key
        self.public_key = public_key

    def resolve_private_key(self, key_id):
        if key_id != KEY_ID or self.private_key is None:
            raise KeyError(key_id)
        return self.private_key

    def resolve_public_key(self, key_id):
        if key_id != KEY_ID or self.public_key is None:
            raise KeyError(key_id)
        return self.public_key


def sign_request(
    url: str,
    body: dict,
    *,
    private_key=None,
    created: datetime.datetime | None = None,
    expires: datetime.datetime | None = None,
    nonce: str | None = None,
) -> requests.PreparedRequest:
    private_key = private_key or load_or_create_private_key()
    request = requests.Request(
        "POST",
        url,
        json=body,
        headers={"Content-Type": "application/json"},
    ).prepare()
    raw = request.body if isinstance(request.body, bytes) else request.body.encode()
    request.headers["Content-Digest"] = content_digest(raw)
    now = datetime.datetime.now(datetime.timezone.utc)
    created = created or now
    if expires is None:
        expires = created + WINDOW
    nonce = nonce or secrets.token_urlsafe(32)
    HTTPMessageSigner(
        signature_algorithm=algorithms.ED25519,
        key_resolver=KeyResolver(private_key=private_key, public_key=private_key.public_key()),
    ).sign(
        request,
        key_id=KEY_ID,
        created=created,
        expires=expires,
        nonce=nonce,
        tag="agent-payer-auth",
        label="sig1",
        covered_component_ids=COVERED,
    )
    return request
