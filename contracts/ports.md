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
| line (`line/server.py`, the Chaperone Line's tools) | 8005 | `127.0.0.1` on the services laptop, public only as `/line/*` through the caregiver app |
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

Storefronts: one merchant service hosts every store in `contracts/merchants.json`. The signed order's `cart.merchant` picks the store (absent → `corner_market`; a `blocked` or unknown id → 403), and a decision made for one store can't buy at another. A decision whose cart spans several stores is placed as **one signed order per store** under the same `decision_id`, each with its `cart.merchant`; each order must be exactly that store's lines (the decision line's `merchant`, or `cart.orders[].merchant`, else the catalog's), and the merchant takes one order per decision per store (409 on a repeat at the same store). A whole-cart order under a decision still works as before. Orders, receipts and events carry `merchant` and `store`; `GET /panel` → `storefronts` says which Cybersource account each store's links use. Biller: `GET /billers/peachtree_power/accounts/PP-2231-0098[?lang=&session_id=]` → the biller facts plus a spoken `say` (posts `bill_checked`). Pay a bill with the pseudo-sku `BILL-peachtree_power`, qty 1, `cart.merchant: "peachtree_power"`; the merchant prices it from `balance_due` (409 when nothing is due), a bill has no pickup code, and a paid bill shows `balance_due: "0.00"` until `/reset`. A bill line refund is refused with `say_key: refund_not_allowed_bill`.

Public through the tunnel, nothing else: the caregiver app, `/s/<session_id>` and `/s/<session_id>/record.json` (the caregiver app serves the relay's `GET /sessions/{id}?format=html` and `/sessions/{id}/record.json`, read-only), `/card/asa` (the caregiver app rewrites it to policy; Lithic's `webhook-signature` HMAC, 5-minute tolerance), `/line/:path*` (rewritten to the line service on port 8005) and `/merchant/webhooks/cybersource` (proxied to the merchant). The raw `/relay/events/stream` rewrite is removed: it exposed every transcript. `/card/simulate` and `/card/holds` stay on the LAN.

Policy route the Host page reads (LAN only, Host header): `GET /approvals/{id}/host_code` answers `{code, expires_at}` for a pending approval, 404 otherwise.

Policy's after-payment routes (8001): `POST /orders/{id}/cancel {mandate_id}` (409 with `say_key: cancel_too_late` once paid); `POST /refunds` answers exactly one of `{preview}`, `{refund}` (the merchant's answer) or `{decision: "deny", say_key, rules}`; `GET /history?mandate_id=&days=`; `POST /mandate/pause`, `POST /mandate/resume/challenge` and `POST /mandate/resume` (caregiver marker; resume also needs a passkey assertion over a single-use challenge that expires in 5 minutes; the pause is stored apart from the signed mandate); `GET /decisions/{id}/explain`. Policy reads an order's status, paid time and card from the merchant's `GET /orders/{id}` before cancel, refund and history.

Line routes (8005), every one but `GET /health` behind the token (`Authorization: Bearer $LINE_MCP_TOKEN`, or the path prefix `/k/<token>` for URL-only MCP clients such as the Voice Agent Builder): `/mcp` (MCP Streamable HTTP, JSON responses; one cart per phone call, kept by the `call_id` every result returns) with the tools `scam_check`, `budget_left`, `search_catalog`, `add_to_cart`, `remove_from_cart`, `read_cart`, `checkout`, `bill_status`, `order_status`, `cancel_order`, `request_refund`, `purchase_history`, each taking an optional `ruth_said`; and `POST /api/<tool>` with the same arguments as JSON (the call keyed by `X-Call-Id`), for the Builder's `api_request` tool. Public as `https://<tunnel-host>/line/*` through the caregiver app's rewrite.
