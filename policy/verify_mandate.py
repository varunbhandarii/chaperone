"""Verify a stored passkey assertion over the mandate hash. Historical assertions use sign count 0."""

from __future__ import annotations

import os

from webauthn import verify_authentication_response
from webauthn.helpers import base64url_to_bytes

from common.config import env
from policy.mandate import mandate_hash


def verify_mandate_assertion(mandate: dict) -> None:
    passkey = mandate.get("passkey") or {}
    response = passkey.get("response")
    public_key = passkey.get("public_key")
    if not response or not public_key:
        raise ValueError("mandate has no passkey assertion")
    host = env("TUNNEL_HOST") or "localhost"
    origin = env("ORIGIN") or (f"http://{host}:5175" if host in {"localhost", "127.0.0.1"} else f"https://{host}")
    verify_authentication_response(
        credential=response,
        expected_challenge=mandate_hash(mandate),
        expected_rp_id=host,
        expected_origin=origin,
        credential_public_key=base64url_to_bytes(public_key),
        credential_current_sign_count=0,
        require_user_verification=os.environ.get("REQUIRE_UV", "1") != "0",
    )
