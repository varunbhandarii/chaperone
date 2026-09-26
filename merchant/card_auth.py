"""Real card authorization for the checkout step (Cybersource REST POST /pts/v2/payments, sandbox).

Why this exists: Pay by Link creation works on our sandbox merchant, but its hosted page's card authorization
fails with reason 150 (usd_outlet_id, usd_terminal_id missing) until Cybersource provisions the account. See
docs/visa-sandbox-auth-error.md. This module lets our own checkout page (/checkout/{order_id}, CARD_AUTH=1)
run a real authorization and mark the order paid only when the response is AUTHORIZED, so the moment the
account is fixed the demo shows a real Visa sandbox authorization with no other code change.

Which merchant authorizes:
  default                      our merchant (VISA_ACCEPTANCE_* credentials).
  CARD_AUTH_MERCHANT_ID,       optional override: another sandbox merchant the team owns (for example a
  CARD_AUTH_API_KEY_ID,        freshly created sandbox account that Cybersource provisioned correctly),
  CARD_AUTH_SECRET_KEY         while payment links keep using the VISA_ACCEPTANCE_* merchant.

Rules:
  * Sandbox only, test cards only. The sandbox forbids real card data, so non-test numbers are refused here
    before any request is made.
  * One request per Pay click, never retried. Reason 150 (system error) is terminal for that attempt, per
    Cybersource's guidance not to resend system errors in a loop.
"""

import json
import os
from dataclasses import asdict, dataclass

import httpx

from merchant.cybs_rest import Creds, signed_request

# Cybersource sandbox test cards (developer.cybersource.com/hello-world/testing-guide.html).
TEST_CARDS = {
    "4111111111111111": "Visa",
    "5555555555554444": "Mastercard",
    "378282246310005": "American Express",
    "6011111111111117": "Discover",
}


@dataclass
class AuthResult:
    ok: bool
    status: str  # AUTHORIZED | DECLINED | SERVER_ERROR | INVALID_REQUEST | REFUSED_LOCALLY | NETWORK_ERROR
    request_id: str | None
    merchant: str  # "ours" or "override"
    reason: str | None = None
    approval_code: str | None = None
    card_brand: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def auth_creds() -> tuple[Creds, str] | None:
    override = [os.environ.get(v, "") for v in ("CARD_AUTH_MERCHANT_ID", "CARD_AUTH_API_KEY_ID", "CARD_AUTH_SECRET_KEY")]
    if all(override):
        return Creds(*override), "override"
    ours = [os.environ.get(v, "") for v in ("VISA_ACCEPTANCE_MERCHANT_ID", "VISA_ACCEPTANCE_API_KEY_ID",
                                            "VISA_ACCEPTANCE_SECRET_KEY")]
    return (Creds(*ours), "ours") if all(ours) else None


def authorize(amount: str, reference: str, card_number: str, exp_month: str, exp_year: str, cvv: str) -> AuthResult:
    found = auth_creds()
    number = "".join(ch for ch in card_number if ch.isdigit())
    if found is None:
        return AuthResult(False, "REFUSED_LOCALLY", None, "", reason="no Cybersource credentials configured")
    creds, which = found
    if number not in TEST_CARDS:
        return AuthResult(False, "REFUSED_LOCALLY", None, which, reason="sandbox accepts Cybersource test cards only")
    body = json.dumps({
        "clientReferenceInformation": {"code": reference[:50]},
        "processingInformation": {"capture": False},
        "paymentInformation": {"card": {"number": number, "expirationMonth": exp_month.zfill(2),
                                        "expirationYear": exp_year if len(exp_year) == 4 else f"20{exp_year}",
                                        "securityCode": cvv}},
        "orderInformation": {
            "amountDetails": {"totalAmount": amount, "currency": "USD"},
            "billTo": {"firstName": "Ruth", "lastName": "Shopper", "address1": "1 Test St", "locality": "Savannah",
                       "administrativeArea": "GA", "postalCode": "31401", "country": "US",
                       "email": "ruth@example.com"},
        },
    })
    try:
        r = signed_request(creds, "POST", "/pts/v2/payments", body, timeout=20)
        d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return AuthResult(False, "NETWORK_ERROR", None, which, reason=f"{type(e).__name__}: {e}")
    status = d.get("status") or f"HTTP_{r.status_code}"
    err = d.get("errorInformation") or {}
    reason = err.get("message") or err.get("reason") or d.get("message")
    approval = (d.get("processorInformation") or {}).get("approvalCode")
    return AuthResult(status == "AUTHORIZED", status, d.get("id"), which,
                      reason=None if status == "AUTHORIZED" else reason, approval_code=approval,
                      card_brand=TEST_CARDS[number])


if __name__ == "__main__":
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    print(json.dumps(authorize("1.00", "chaperone-card-auth-check", "4111111111111111", "12", "2030", "123").to_dict(),
                     indent=2))
