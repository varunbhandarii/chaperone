"""Ephemeral client secrets for the Grok Voice realtime API.

The browser never sees XAI_API_KEY. It asks the relay for a short-lived client secret and
opens the WebSocket with the subprotocol "xai-client-secret.<value>".

xAI endpoint: POST https://api.x.ai/v1/realtime/client_secrets
  request:  {"expires_after": {"seconds": 300}}          (max 3600, default 600)
  response: {"value": "xai-realtime-client-secret-...", "expires_at": <unix seconds>}
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from common import tls

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH)

CLIENT_SECRETS_URL = "https://api.x.ai/v1/realtime/client_secrets"
MIN_SECONDS = 10
MAX_SECONDS = 3600

# Tests replace this with an httpx.MockTransport; None means the real network.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class TokenError(RuntimeError):
    """Minting failed. `status` is the HTTP status the relay should answer with."""

    status = 502


class MissingKeyError(TokenError):
    status = 503


def api_key() -> str:
    """Current XAI_API_KEY. Re-reads .env when the key is missing, so adding it needs no restart."""
    key = os.getenv("XAI_API_KEY", "").strip()
    if not key:
        load_dotenv(ENV_PATH, override=False)
        key = os.getenv("XAI_API_KEY", "").strip()
    return key


def has_key() -> bool:
    return bool(api_key())


def _upstream_message(response: httpx.Response) -> str:
    """Short, secret-free description of an upstream error body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or err)[:200]
        if err:
            return str(err)[:200]
        if body.get("message"):
            return str(body["message"])[:200]
    return str(body)[:200]


async def mint_client_secret(seconds: int = 300) -> dict:
    """Mint an ephemeral client secret. Returns {"value", "expires_at"}.

    Raises MissingKeyError when XAI_API_KEY is not configured and TokenError when xAI refuses
    or cannot be reached. Error messages never contain the key or the minted secret.
    """
    key = api_key()
    if not key:
        raise MissingKeyError(
            f"XAI_API_KEY is not set. Add it to {ENV_PATH}; the relay reads it on the next request."
        )
    seconds = max(MIN_SECONDS, min(MAX_SECONDS, int(seconds)))
    try:
        async with httpx.AsyncClient(verify=tls.context(), timeout=10.0, transport=TRANSPORT) as client:
            response = await client.post(
                CLIENT_SECRETS_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"expires_after": {"seconds": seconds}},
            )
    except httpx.HTTPError as exc:
        raise TokenError(f"could not reach api.x.ai: {type(exc).__name__}") from None

    if response.status_code != 200:
        raise TokenError(f"api.x.ai answered {response.status_code}: {_upstream_message(response)}")
    try:
        data = response.json()
    except ValueError:
        raise TokenError("api.x.ai returned a non-JSON body") from None
    value, expires_at = data.get("value"), data.get("expires_at")
    if not isinstance(value, str) or not value or expires_at is None:
        raise TokenError("api.x.ai response is missing value or expires_at")
    return {"value": value, "expires_at": expires_at}
