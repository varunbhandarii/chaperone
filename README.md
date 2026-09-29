# Chaperone

A voice helper for Ruth that can only spend inside rules her son Priyank signed with a passkey. She talks at a station, or calls the Chaperone Line. Policy, not the model, decides allow, approve, or deny. An allow is signed and checked by the merchant before a Visa sandbox payment link is created, one link per store. Her card is checked on every swipe. A scam story is refused out loud, Priyank's phone is told, and the card can go on a cool-down.

Copy `.env.example` to `.env` and fill it from the shared vault. Never commit `.env`, `keys/`, or `sessions/*.json`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

The agent signing key is `keys/agent_ed25519.pem`. The matching public key is `relay/jwks.json`. On a new laptop, `python -c "from signer.keys import public_jwk, load_jwks; print(public_jwk()['x'] == load_jwks()['keys'][0]['x'])"` must print `True`.

`POLICY_CODE_KEY` must be set or policy will not start. `POLICY_DEV_KEY_OK=1` allows the public default, for tests only. For a laptop smoke test set `JUDGE_FAKE=1`, `MOCK_VISA=1`, and `MERCHANT_VERIFY=enforce`. `MANDATE_UNSIGNED_OK=1` lets checkout run before a passkey mandate is stored. On the team router, point `MERCHANT_PUBLIC_URL` at `http://192.168.8.10:8002`.

## Services

Run each in its own terminal, from the repo root unless noted. Ports are in `contracts/ports.md`.

```bash
# catalog :8003
.venv/bin/python -m catalog.seed_openfda    # optional, no key
.venv/bin/python -m catalog.seed_kroger     # optional, needs KROGER_*
.venv/bin/python -m catalog.build_catalog
.venv/bin/python -m uvicorn catalog.search:app --host 0.0.0.0 --port 8003

# policy :8001  (checkout, screen, judge, scam check, card, mandate)
.venv/bin/python -m uvicorn policy.main:app --host 0.0.0.0 --port 8001

# merchant :8002  (one service, every store in contracts/merchants.json)
.venv/bin/python -m uvicorn merchant.orders:app --host 0.0.0.0 --port 8002

# relay :8000  (voice tokens, ledger, Trust Ledger, terminal, JWKS, audio)
.venv/bin/python -m uvicorn relay.main:app --host 0.0.0.0 --port 8000

# line :8005  (phone tools; see line/README.md)
.venv/bin/python -m uvicorn line.server:app --host 127.0.0.1 --port 8005

# station :5173
cd station/kiosk && npm install && npm run dev

# printer helper :8004  (optional; PDF when no printer is attached)
.venv/bin/python -m uvicorn station.printer:app --host 127.0.0.1 --port 8004

# caregiver :5175  (passkeys; the phone must use the fixed ngrok host)
cd caregiver && npm install && npm run dev
ngrok http 5175 --url https://<tunnel-host>
```

`TUNNEL_HOST` in `.env` is that hostname with no scheme. Do not change it after the passkey is registered. The caregiver app rewrites `/card/asa` to policy and `/line/*` to the line service, so Lithic and the phone line use the same tunnel.

Open the station at http://localhost:5173 (the microphone requires localhost). Open the Trust Ledger at http://localhost:8000/wall and the card terminal at http://localhost:8000/terminal (LAN only). Priyank's phone opens `https://<tunnel-host>`.

## Who does what

Ruth speaks in English, Spanish, or Hindi. The station reads the cart back and waits for a yes before checkout. Prices come from the catalog, including a live Kroger lookup when the snapshot has no match. The model never invents a price.

Priyank signs the mandate on his phone: caps, stores, blocked categories, and what needs his approval. He can pause the agent, approve or reject with a passkey (Chrome can show the amount and the store before the fingerprint), allow a card hold once, or keep a blocked hold blocked. Gift-card and prepaid lines belong to `quickgift_cards` and are refused.

Stores in `contracts/merchants.json`: Corner Market, Parkside Pharmacy, Main Street Home, and Peachtree Power. A cart that spans stores becomes one signed order per store under the same decision. A power-bill line (`BILL-peachtree_power`) is priced from the biller. A bill cannot be refunded.

Ruth's card is a Lithic sandbox card. Every swipe hits `POST /card/asa` on the tunnel. `python -m policy.card_setup` creates the card if needed and enrolls `https://$TUNNEL_HOST/card/asa`. Run that only on the machine that should receive swipes. The card number is written to `sessions/card.json` and is not printed.

The Chaperone Line is a phone number answered by Grok. Purchases, cancels, refunds, and paying a bill need `verify_pin` (`LINE_PIN` in `.env`). The PIN never goes on the ledger. Details are in `line/README.md`.

## What a checkout does

The station searches `GET /search` and `GET /resolve` on the catalog, then calls policy. Policy re-prices the cart, screens the words, runs the mandate, and on allow signs `POST /orders` for each store. The merchant checks the signature and the decision id, then returns a payment link. A purchase over the approval threshold waits for Priyank. Events go to `POST /events` on the relay. `curl -N http://localhost:8000/events/stream` shows them live.

`POST /orders/{id}/paid` (with `X-Chaperone-Host: 1`; the Host page's Confirm payment sends it) and `python -m merchant.simulate_payment <order_id>` mark an order paid when the sandbox card cannot authorize. `python -m merchant.spike_link` creates a demo link. `curl -X POST -H "X-Chaperone-Host: 1" http://localhost:8000/reset` (from the LAN) clears the ledger, the monthly spend, card holds, the cool-down, and merchant orders.

## Tests

```bash
.venv/bin/pytest -q catalog merchant relay policy line common
cd station/kiosk && npm test
cd caregiver && node --test lib/*.test.js
```

Scam-eval results are in `ai/eval/RESULTS.md`. The radar probe is in `ai/eval/RADAR_PROBE.md`.

## Notes for a reviewer

`docs/security-evidence.md` records the static scan and the controls the tests cover. It is not a PCI, ISO, NIST, or SOC 2 certification. `docs/visa-merchants.md` and `docs/visa-probes.md` say which Visa sandbox calls are real and which are not.
