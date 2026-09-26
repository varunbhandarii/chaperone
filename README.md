# chaperone
## Merchant + catalog

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # fill VISA_ACCEPTANCE_* (or MOCK_VISA=1), KROGER_* optional

.venv/bin/python -m catalog.seed_openfda   # OTC labels -> catalog/raw/openfda.json (no key)
.venv/bin/python -m catalog.seed_kroger    # needs KROGER_* ; skipped otherwise
.venv/bin/python -m catalog.build_catalog  # -> catalog/catalog.json

.venv/bin/python -m uvicorn catalog.search:app --host 0.0.0.0 --port 8003
.venv/bin/python -m uvicorn merchant.orders:app --host 0.0.0.0 --port 8002

.venv/bin/python -m merchant.spike_link           # demo cart $11.49 -> payment link, opens it
.venv/bin/python -m merchant.spike_link --direct  # $1.00 link straight through the Visa MCP (checks creds)
.venv/bin/python -m pytest -q catalog merchant
```

Catalog API: `/search?q=`, `/resolve?q=` (profile: "my blood pressure medicine" -> RX-001, "bread" -> BAK-001), `/suggest?sku=&budget=` (alternatives with reasons, same active ingredient for medicines, never for gift cards or prescriptions), `/items/{sku}`.
Merchant API: `POST /orders` `{mandate_id, decision_id, session_id, approval_id, cart:{items:[{sku, qty}]}}` -> order with `payment_link.url`; prices come from the catalog, never the agent. `POST /orders/{id}/paid` is the callback fallback; `/pay/{link_id}` is the mock hosted page; `/panel` feeds the wall.
