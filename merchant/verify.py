"""Merchant-side check of the agent's RFC 9421 signature.

Placeholder until the signer/verifier lands (contracts/signing.md + relay JWKS). The order flow
already calls verify_request() and shows every check on the wall, so wiring the real verifier only
replaces the body of this function.

Real checks to add (PRD "Signed order request headers"):
  signature valid for keyid against the relay JWKS, tag == "agent-payer-auth", alg ed25519
  Content-Digest matches the body
  expires <= created + 8 min and now inside the window
  nonce unseen (8-minute store)
  decision_id is allow/approved on the ledger

MERCHANT_VERIFY=off (default for now) records the check as skipped instead of failing orders.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Verification:
    ok: bool
    keyid: str | None = None
    checks: list[dict] = field(default_factory=list)


def verify_request(method: str, authority: str, path: str, headers: dict[str, str], body: bytes) -> Verification:
    mode = os.environ.get("MERCHANT_VERIFY", "off")
    has_sig = "signature" in headers and "signature-input" in headers
    if mode == "off":
        detail = "verifier not wired yet (MERCHANT_VERIFY=off)"
        if has_sig:
            detail += "; signature headers present"
        return Verification(ok=True, checks=[{"id": "signature", "passed": None, "detail": detail}])
    return Verification(ok=False, checks=[{"id": "signature", "passed": False, "detail": "enforce mode but no verifier wired"}])
