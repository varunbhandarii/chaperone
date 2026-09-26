# Ports

Frozen for the weekend. A change needs all four people at the table.

| Service | Port | Bind |
|---|---|---|
| relay | 8000 | `0.0.0.0` |
| policy | 8001 | `0.0.0.0` |
| merchant | 8002 | `0.0.0.0` |
| catalog | 8003 | `0.0.0.0` |
| station | 5173 | `0.0.0.0` |
| caregiver | 5175 | tunnel (`ngrok http 5175 --url https://<tunnel-host>`) |
| wall | retired | served by the relay at `GET /wall` (same origin as the event stream) |

LAN reservations: services `192.168.8.10`, station `192.168.8.11`, caregiver phone `192.168.8.20`.

Service endpoints each part calls:

- Catalog for the station: `GET http://192.168.8.10:8003/search?q=&limit=` answers `{"q", "items": [...]}`
- Profile phrases for the station: `GET http://192.168.8.10:8003/resolve?q=` answers `{"q", "matches": [{sku, label, note, item}]}`
- Policy for the station and merchant: `POST http://192.168.8.10:8001/checkout`
- Decision lookup for the merchant: `GET http://192.168.8.10:8001/decisions/{decision_id}`
- Merchant for the signer: `POST http://192.168.8.10:8002/orders`
- Judge for policy: `POST http://192.168.8.10:8001/judge` (score `0.05` when `XAI_API_KEY` is unset and `JUDGE_FAKE=1`)
- Ledger for everyone: `POST http://192.168.8.10:8000/events` (shape in `contracts/events.schema.json`, answers 202)

Relay routes (8000). `relay/main.py` owns the token routes; `relay/ledger.py` owns the rest:

| Route | What |
|---|---|
| `POST /session/token`, `GET /health` | Grok Voice client secrets |
| `POST /events` | validate, add `rt` and `seq`, append to `sessions/<session_id>.jsonl` and `sessions/live.jsonl`, fan out |
| `GET /events/stream?session_id=&types=` | SSE, `id: <seq>`, replay from `Last-Event-ID`, `:` heartbeat every 15 s |
| `GET /sessions/{id}` | that session's events as JSON |
| `GET /jwks.json`, `GET /.well-known/jwks.json` | `relay/jwks.json` |
| `GET /audio/*` | refusal clips from `ai/warnings/` |
| `GET /wall` | the wall page |
| `POST /reset` | clear the live ledger, then call policy and merchant `/reset` |

Merchant routes (8002): `POST /orders`, `GET /orders[/{id}]`, `POST /orders/{id}/paid`, `GET /pay/{link_id}` (mock page), `GET /panel`, `POST /reset`, `POST /webhooks/cybersource`, `GET|POST /webhooks/cybersource/health`.

Public through the tunnel, nothing else: the caregiver app, `/relay/events/stream` (proxied to the relay) and `/merchant/webhooks/cybersource` (proxied to the merchant).
