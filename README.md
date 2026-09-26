# Chaperone

A voice shopping agent that can only spend inside a caregiver-signed mandate. The shopper talks at the station. Policy, not the model, decides allow, approve, or deny. An allow is signed and checked by the merchant before a sandbox payment link is created. Refusals are spoken, and the caregiver's phone is told.

Copy `.env.example` to `.env` and fill it from the shared vault. Never commit `.env` or `keys/`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

The agent signing key is `keys/agent_ed25519.pem`. The matching public key is `relay/jwks.json`. On a new laptop, `python -c "from signer.keys import public_jwk, load_jwks; print(public_jwk()['x'] == load_jwks()['keys'][0]['x'])"` must print `True`.

For a laptop smoke test set `JUDGE_FAKE=1`, `MOCK_VISA=1`, and `MERCHANT_VERIFY=enforce`. `MANDATE_UNSIGNED_OK=1` lets checkout run before a passkey mandate is stored. On the team router, point `MERCHANT_PUBLIC_URL` at `http://192.168.8.10:8002`.

## Services

Run each in its own terminal, from the repo root unless noted. Ports are in `contracts/ports.md`.

```bash
# catalog :8003
.venv/bin/python -m catalog.seed_openfda    # optional, no key
.venv/bin/python -m catalog.seed_kroger     # optional, needs KROGER_*
.venv/bin/python -m catalog.build_catalog
.venv/bin/python -m uvicorn catalog.search:app --host 0.0.0.0 --port 8003

# policy :8001  (checkout, screen, judge, budget, mandate)
.venv/bin/python -m uvicorn policy.main:app --host 0.0.0.0 --port 8001

# merchant :8002
.venv/bin/python -m uvicorn merchant.orders:app --host 0.0.0.0 --port 8002

# relay :8000  (voice tokens, ledger, wall, JWKS, refusal audio)
.venv/bin/python -m uvicorn relay.main:app --host 0.0.0.0 --port 8000

# station :5173
cd station/kiosk && npm install && npm run dev

# caregiver :5175  (passkeys; the phone must use the fixed ngrok host)
cd caregiver && npm install && npm run dev
ngrok http 5175 --url https://<tunnel-host>
```

`TUNNEL_HOST` in `.env` is that hostname with no scheme. Do not change it after the passkey is registered.

Open the station at http://localhost:5173 (the microphone requires localhost). Open the wall at http://localhost:8000/wall. The caregiver phone opens `https://<tunnel-host>`.

## What a checkout does

The station searches `GET /search` and `GET /resolve` on the catalog, then calls `POST /screen` and `POST /checkout` on policy. Policy re-prices the cart from the catalog, runs the mandate rules, and on allow signs `POST /orders`. The merchant checks the signature and the decision id, then returns a payment link. Events go to `POST /events` on the relay. `curl -N http://localhost:8000/events/stream` shows them live.

`POST /orders/{id}/paid` and `python -m merchant.simulate_payment <order_id>` mark an order paid when the sandbox card cannot. `python -m merchant.spike_link` creates the demo link. `curl -X POST http://localhost:8000/reset` clears the ledger, the monthly spend, and merchant orders.

## Tests

```bash
.venv/bin/pytest -q catalog merchant relay policy
cd station/kiosk && npm test
```
