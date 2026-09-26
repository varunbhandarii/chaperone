# chaperone
## Merchant, catalog, relay ledger and wall

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # fill VISA_ACCEPTANCE_* (or MOCK_VISA=1), KROGER_* optional

.venv/bin/python -m catalog.seed_openfda   # OTC labels -> catalog/raw/openfda.json (no key)
.venv/bin/python -m catalog.seed_kroger    # needs KROGER_* ; skipped otherwise
.venv/bin/python -m catalog.build_catalog  # -> catalog/catalog.json

.venv/bin/python -m uvicorn catalog.search:app --host 0.0.0.0 --port 8003
.venv/bin/python -m uvicorn merchant.orders:app --host 0.0.0.0 --port 8002

# relay/main.py includes relay/ledger.py; standalone ledger for a laptop test: relay.ledger:app
.venv/bin/python -m uvicorn relay.main:app --host 0.0.0.0 --port 8000
open http://localhost:8000/wall                   # the wall: mandate, live ledger, merchant check
curl -N http://localhost:8000/events/stream       # raw live stream

.venv/bin/python -m merchant.spike_link           # demo cart $11.49 -> payment link, opens it
.venv/bin/python -m merchant.spike_link --direct  # $1.00 link straight through the Visa MCP (checks creds)
.venv/bin/python -m merchant.simulate_payment <order_id>  # signed Cybersource webhook -> order paid
.venv/bin/python -m merchant.cybs_check           # $1.00 sandbox authorization -> AUTHORIZED or the reason
curl -X POST http://localhost:8000/reset          # demo reset: ledger, policy spend, merchant orders
.venv/bin/python -m pytest -q catalog merchant relay
```

Catalog API: `/search?q=`, `/resolve?q=` (profile: "my blood pressure medicine" -> RX-001, "bread" -> BAK-001), `/suggest?sku=&budget=` (alternatives with reasons, same active ingredient for medicines, never for gift cards or prescriptions), `/items/{sku}`.
Merchant API: `POST /orders` `{mandate_id, decision_id, session_id, approval_id, cart:{items:[{sku, qty}]}}` -> order with `payment_link.url`; prices come from the catalog, never the agent. `POST /orders/{id}/paid` is the callback fallback; `/pay/{link_id}` is the mock hosted page; `/panel` feeds the wall; `POST /reset`; `POST /webhooks/cybersource` (v-c-signature HMAC, see `merchant/webhooks.py`).
Relay ledger: `POST /events` (contracts/events.schema.json, 202), `GET /events/stream` (SSE, Last-Event-ID replay), `GET /sessions/{id}`, `GET /jwks.json`, `GET /audio/{clip}`, `GET /wall`, `POST /reset`. Full list in `contracts/ports.md`.
