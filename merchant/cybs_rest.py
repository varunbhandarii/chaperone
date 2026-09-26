"""Cybersource REST with HTTP Signature auth (HmacSHA256 over host, date, request-target, digest, merchant id).

Shared by the auth diagnostics (merchant.cybs_check) and the card-authorization checkout (merchant.card_auth).
"""

import base64
import hashlib
import hmac
from dataclasses import dataclass
from email.utils import formatdate

import httpx

SANDBOX_HOST = "apitest.cybersource.com"


@dataclass(frozen=True)
class Creds:
    merchant_id: str
    key_id: str
    secret_b64: str  # the Shared Secret from Key Management, base64 as shown in the Business Center
    host: str = SANDBOX_HOST


def signed_request(creds: Creds, method: str, path: str, body: str | None = None, timeout: float = 30) -> httpx.Response:
    headers = {"host": creds.host, "date": formatdate(usegmt=True), "v-c-merchant-id": creds.merchant_id}
    signed = ["host", "date", "request-target"]
    if body is not None:
        headers["digest"] = "SHA-256=" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        headers["content-type"] = "application/json"
        signed.append("digest")
    signed.append("v-c-merchant-id")
    lines = [f"request-target: {method.lower()} {path}" if h == "request-target" else f"{h}: {headers[h]}"
             for h in signed]
    sig = base64.b64encode(
        hmac.new(base64.b64decode(creds.secret_b64), "\n".join(lines).encode(), hashlib.sha256).digest()
    ).decode()
    headers["signature"] = (f'keyid="{creds.key_id}", algorithm="HmacSHA256", headers="{" ".join(signed)}", '
                            f'signature="{sig}"')
    return httpx.request(method, f"https://{creds.host}{path}", headers=headers, content=body, timeout=timeout)
