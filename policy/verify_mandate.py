"""Verify a passkey assertion over the mandate hash against the pinned caregiver key."""

from __future__ import annotations

import json
import os

from jsonschema import Draft202012Validator, RefResolver
from webauthn import verify_authentication_response
from webauthn.helpers import base64url_to_bytes

from common.config import ROOT, env
from policy.mandate import mandate_hash
from policy.store import load_caregiver_credential, save_caregiver_credential

_SCHEMA = json.loads((ROOT / "contracts" / "mandate.schema.json").read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA, resolver=RefResolver(base_uri=(ROOT / "contracts").as_uri() + "/", referrer=_SCHEMA))


def verify_mandate_assertion(mandate: dict) -> None:
    _VALIDATOR.validate(mandate)
    passkey = mandate.get("passkey") or {}
    response = passkey.get("response") or {}
    credential_id = passkey.get("credential_id")
    public_key = passkey.get("public_key")
    if credential_id != response.get("id"):
        raise ValueError("credential id does not match the assertion")
    pinned = load_caregiver_credential()
    if pinned and pinned["credential_id"] != credential_id:
        raise ValueError("credential is not the pinned caregiver")
    key = pinned["public_key"] if pinned else public_key
    host = env("TUNNEL_HOST") or "localhost"
    origin = env("ORIGIN") or (f"http://{host}:5175" if host in {"localhost", "127.0.0.1"} else f"https://{host}")
    verified = verify_authentication_response(
        credential=response,
        expected_challenge=mandate_hash(mandate),
        expected_rp_id=host,
        expected_origin=origin,
        credential_public_key=base64url_to_bytes(key),
        credential_current_sign_count=int((pinned or {}).get("sign_count") or 0),
        require_user_verification=os.environ.get("REQUIRE_UV", "1") != "0",
    )
    if not pinned:
        save_caregiver_credential({
            "credential_id": credential_id,
            "public_key": public_key,
            "sign_count": verified.new_sign_count,
        })
    else:
        pinned["sign_count"] = verified.new_sign_count
        save_caregiver_credential(pinned)
