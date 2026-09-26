"""Approval records. The nonce and code never leave this module."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

import jcs

from policy.store import load_decisions, save_decision

PUBLIC_FIELDS = ("approval_id", "expires_at", "amount", "merchant", "excerpt", "rule", "decision_id")


def code_key() -> bytes:
    configured = os.environ.get("POLICY_CODE_KEY")
    if configured:
        return configured.encode()
    return b"chaperone-dev-code-key"


def code_mac(code: str, approval_id: str) -> str:
    return hmac.new(code_key(), f"{code}{approval_id}".encode(), hashlib.sha256).hexdigest()


def new_nonce() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")


def challenge_bytes(approval: dict) -> bytes:
    payload = {
        "approval_id": approval["approval_id"],
        "amount": approval["amount"],
        "merchant": approval["merchant"],
        "nonce": approval["nonce"],
        "expires_at": approval["expires_at"],
    }
    return hashlib.sha256(jcs.canonicalize(payload)).digest()


def _expires(approval: dict) -> datetime:
    return datetime.fromisoformat(approval["expires_at"])


def state_of(approval: dict, now: datetime | None = None) -> str:
    if approval.get("approved") is True:
        return "approved"
    if approval.get("approved") is False:
        return "rejected"
    now = now or datetime.now(timezone.utc)
    if now > _expires(approval):
        return "expired"
    return "pending"


def find_approval(approval_id: str) -> dict | None:
    for document in load_decisions().values():
        approval = document.get("approval") or {}
        if approval.get("approval_id") == approval_id:
            return document
    return None


def public_approval(document: dict) -> dict:
    approval = document.get("approval") or {}
    view = {key: approval.get(key) for key in PUBLIC_FIELDS}
    view["state"] = state_of(approval)
    view["order"] = document.get("order")
    view["decision_id"] = document.get("decision_id")
    return view


def public_decision(document: dict) -> dict:
    hidden = {"code_hash", "nonce", "attempts", "used"}
    approval = document.get("approval")
    projected = {key: value for key, value in document.items() if key != "approval"}
    if approval:
        projected["approval"] = {key: value for key, value in approval.items() if key not in hidden}
    return projected
