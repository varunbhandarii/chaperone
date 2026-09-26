# Signing convention

RFC 9421 over the order request, Ed25519, header signatures only. The Python library is `http-message-signatures` 2.0.1. Body-digest and nonce replay are not done by the library; `merchant/verify.py` does both.

## Covered components

`@method`, `@authority`, `@path`, `content-digest`, `content-type`

## Signature parameters

| Parameter | Value |
|---|---|
| label | `sig1` |
| keyid | `chaperone-agent-1` |
| alg | `ed25519` (lower case, the value the library accepts) |
| created | Unix seconds, UTC |
| expires | `created` + 8 minutes |
| nonce | `secrets.token_urlsafe(32)`, stored for 8 minutes, single use |
| tag | `agent-payer-auth` for orders |

## Headers

```http
Content-Type: application/json
Content-Digest: sha-256=:<base64>:
Signature-Input: sig1=("@method" "@authority" "@path" "content-digest" "content-type");created=...;expires=...;keyid="chaperone-agent-1";alg="ed25519";nonce="...";tag="agent-payer-auth"
Signature: sig1=:<base64>:
```

`Content-Digest` is RFC 9530, built with `http_sf.compat.Dictionary({"sha-256": sha256(raw_body).digest()})`. The verifier recomputes SHA-256 over the raw body and compares it to that header. One changed byte fails this check. The signature covers the digest header, not the body bytes themselves.

## Keys

- Private key: `keys/agent_ed25519.pem` (gitignored). Copy it into the vault. Do not commit it.
- Public JWK: `relay/jwks.json` as `{"keys":[{"kty":"OKP","crv":"Ed25519","x":"...","kid":"chaperone-agent-1"}]}`.
- Also served at `GET /jwks.json` and `GET /.well-known/jwks.json` on the relay (port 8000).

## Mandate hash

The passkey challenge is `SHA-256(jcs.canonicalize(mandate without the passkey field))` (RFC 8785). The assertion is stored beside the mandate, not inside the bytes that were hashed.

The approval challenge is `SHA-256(JCS({approval_id, amount, merchant, nonce, expires_at}))`. `nonce` is 16 bytes from the server, hex encoded, and single use. Two approvals for the same amount do not share a challenge.

## Verifier checks, in order

1. Recomputed body digest equals `Content-Digest`.
2. `HTTPMessageVerifier.verify(..., max_age=8 minutes, expect_tag="agent-payer-auth")`.
3. `expires - created <= 8 minutes`.
4. Nonce unseen in the last 8 minutes.
5. `keyid` is `chaperone-agent-1`.
6. `decision_id` in the body is on the ledger with `allow`, or `approve` after the caregiver approved it.

A second POST with the same nonce returns `rejected: replay`. An `expires` in the past is rejected by the library.
