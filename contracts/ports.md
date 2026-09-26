# Ports

Frozen for the weekend. A change needs all four people at the table.

| Service | Port | Bind |
|---|---|---|
| relay | 8000 | `0.0.0.0` |
| policy | 8001 | `0.0.0.0` |
| merchant | 8002 | `0.0.0.0` |
| catalog | 8003 | `0.0.0.0` |
| station | 5173 | `0.0.0.0` |
| printer helper (`station/printer.py`) | 8004 | `127.0.0.1` on the station laptop, via the Vite proxy `/svc/printer` |
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
| `POST /events` | validate, add `rt` and `seq`, append to `sessions/by-id/<session_id>.jsonl` and `sessions/live.jsonl`, fan out |
| `GET /events/stream?session_id=&types=&since=` | SSE, `id: <seq>`, replay from `Last-Event-ID`, `:` heartbeat 15 s after the last write; `since=<ms>` skips replayed events older than that |
| `GET /sessions/{id}/record.json` | the dispute-ready record: shopper's words, mandate hash, decisions, signatures, orders, payments, refunds |
| `GET /sessions/{id}[?format=html]` | that session's events as JSON; `format=html` is the read-only page behind the receipt's QR code |
| `GET /jwks.json`, `GET /.well-known/jwks.json` | `relay/jwks.json` |
| `GET /audio/*` | refusal clips from `ai/warnings/` |
| `GET /wall` | the wall page |
| `POST /reset` | call policy and merchant `/reset` in parallel, clear the live ledger, post `reset`; answers `{ok, policy, merchant, failed, ms}`. Needs `X-Chaperone-Host: 1` |
| `GET /host`, `/host/api/*` | the Host's controls, LAN only (403 through a proxy); every POST needs `X-Chaperone-Host: 1`: confirm payment, reset, arm replay, fallback code |

Merchant routes (8002): `POST /orders` (409 when the decision already has an order), `POST /orders/{id}/cancel` and `POST /orders/{id}/refunds` (both RFC 9421 signed, five checks), `POST /orders/{id}/picked-up` (Host header), `GET /orders[/{id}][?session_id=]`, `GET /orders/{id}/receipt[?lang=]`, `POST /orders/{id}/paid` and `POST /reset` (both Host header), `GET /pay/{link_id}` (mock page), `GET /panel`, `POST /webhooks/cybersource`, `GET|POST /webhooks/cybersource/health`.

Public through the tunnel, nothing else: the caregiver app, `/s/<session_id>` and `/s/<session_id>/record.json` (the caregiver app serves the relay's `GET /sessions/{id}?format=html` and `/sessions/{id}/record.json`, read-only) and `/merchant/webhooks/cybersource` (proxied to the merchant). The raw `/relay/events/stream` rewrite is removed: it exposed every transcript.

Policy route the Host page reads (LAN only, Host header): `GET /approvals/{id}/host_code` answers `{code, expires_at}` for a pending approval, 404 otherwise.

Policy's after-payment routes (8001): `POST /orders/{id}/cancel {mandate_id}` (409 with `say_key: cancel_too_late` once paid); `POST /refunds` answers exactly one of `{preview}`, `{refund}` (the merchant's answer) or `{decision: "deny", say_key, rules}`; `GET /history?mandate_id=&days=`; `POST /mandate/pause`, `POST /mandate/resume/challenge` and `POST /mandate/resume` (caregiver marker; resume also needs a passkey assertion over a single-use challenge that expires in 5 minutes; the pause is stored apart from the signed mandate); `GET /decisions/{id}/explain`. Policy reads an order's status, paid time and card from the merchant's `GET /orders/{id}` before cancel, refund and history.
