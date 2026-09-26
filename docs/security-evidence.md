# Security evidence

Scanned and tested on 26 September 2026. This is evidence for the submission. It is not a PCI DSS, ISO 27001, NIST, OWASP ASVS, or SOC 2 certification.

## 1. Static analysis

**Python.** Bandit 1.9.4 on Python 3.12.13:

```text
bandit -r policy signer common -x '*/tests/*' -ll
```

1,982 lines scanned. No low, medium, or high findings. Nothing was skipped with `#nosec`.

**JavaScript.** `npm audit --omit=dev` in `caregiver/` (Next.js 15.5):

| Package | Severity | Note |
|---|---|---|
| `postcss` <= 8.5.22, pulled in by `next` | 1 high, 1 moderate | XSS and source-map path issues inside the Next build toolchain |

The suggested fix upgrades Next to 16, which is a breaking change. The findings are in the build tool, not in the passkey or approval routes. They are recorded here and left unfixed on purpose.

## 2. Controls a reviewer can check

These are practices named by OWASP (session handling, access control, input checks, output encoding). The table is not an ASVS score.

| Control | Where | Test |
|---|---|---|
| Passkey required to sign the mandate and to approve | `policy/verify_mandate.py`, `policy/main.py` `decide` | `policy/tests/test_mandate.py`, `policy/tests/test_checkout.py` |
| Sign-in cookie is httpOnly, Secure, SameSite=Strict, 30 minutes. Approvals, reject, codes, and alerts return 401 without it | `caregiver/lib/session.js`, `caregiver/lib/challenges.js` | Live check: `GET /api/approvals` without a cookie is 401 |
| A reject or a Chrome payment approval needs a server HMAC. A forged header is refused | `policy/approvals.py` `marker_matches` | `test_rejection_closes_the_approval`, `test_forged_marker_cannot_reject`, `test_payment_marker_places_the_order_without_a_webauthn_get` |
| Approval codes are HMAC'd, compared in constant time, and five failures close the approval. The code is not in `GET /decisions` | `policy/approvals.py`, `policy/main.py` `submit_code` | `test_five_wrong_codes_lock_the_approval`, `test_decision_lookup_hides_the_approval_code` |
| The host code requires `X-Chaperone-Host: 1` | `policy/main.py` `_host_header` | `test_host_page_code_approves_and_then_disappears` |
| Approval ids reject `../` and markup. Session ids reject the same. Session HTML escapes `<script>` | `caregiver/lib/ids.js`, `caregiver/app/s/[id]/route.js` | `caregiver/lib/ids.test.js` |
| `GET /mandate` does not return the stored passkey assertion | `policy/main.py` `get_mandate` | `test_mandate_read_drops_the_assertion` |
| Policy will not boot on the public default code key unless `POLICY_DEV_KEY_OK=1` | `policy/main.py` startup | The demo process is running with `POLICY_CODE_KEY` set |

On 26 September 2026 those tests were run together: 32 policy tests passed, and 7 caregiver tests passed (`ids.test.js`, `setup.test.js`).

## 3. Passkeys and NIST digital identity

NIST SP 800-63B describes authenticator assurance in terms of a cryptographic authenticator, user verification, and a fresh challenge so an old signature cannot be reused. Chaperone's caregiver path follows that shape. It is not an AAL2 assessment.

- Priyank's passkey is bound to the tunnel hostname. The phone signs a server-made challenge. Policy checks the signature, the origin, and user verification.
- Resume (turning the agent back on) is a new challenge over `{action, mandate_id, nonce, expires_at}`, not a replay of the mandate signature (`policy/postpurchase.py` `resume_challenge`).
- Chrome's payment dialog signs `payment.get` over the amount and "Corner Market". The caregiver server checks the type, the total, and the payee before policy will place the order.
- The six-digit host code is a shared secret for when the passkey cannot be used. That path is not the cryptographic authenticator above. It is rate-limited, kept off the phone, and hidden from `GET /decisions`.
