# Spoken lines

`lines.<lang>.json` (en, es, hi) is the single source for every fixed line the station speaks and for the refusal text `/screen` returns. The three files have the same keys and the same `{placeholders}`; a test checks both.

| Key | Placeholders | When |
|---|---|---|
| `ordering_now` | `{total}` | checkout allowed |
| `asking_priya` | | over the approval threshold; clip `line.asking_priya` |
| `caregiver_approved`, `caregiver_declined`, `caregiver_timeout` | | approval outcome |
| `receipt_done`, `receipt_on_screen` | `{total}` | receipt printed, or shown on screen; clip `line.receipt_done` from `receipt_done_clip` (no amount) |
| `you_saved`, `loyalty_points` | `{saved}`, `{points}` | after the receipt line; leave `you_saved` out when nothing was saved |
| `order_ready` | `{pickup_code}` | "where is my order?" once it is ready; before that the station speaks its own `order_status` line with the live status |
| `order_cancelled`, `cancel_too_late` | | cancel on an unpaid or a paid order |
| `refund_preview`, `refund_done` | `{amount}`, `{card_last4}` | return read-back, then done; clip `line.refund_done` from `refund_done_clip` (no amount) |
| `refund_not_allowed_rx` | | a prescription return (RF4) |
| `agent_paused` | | the mandate is paused |
| `checkout_unavailable`, `over_monthly_cap`, `read_back_required`, `declined`, `cart_empty` | | checkout outcomes |
| `budget_left` | `{left}` | budget question |
| `repeat_nothing` | | "repeat that" before anything was said |
| `blocked_category`, `scam_pattern`, `code_reading`, `refund_scam` | | refusals; clips `refusal.<key>` |

`repeat_triggers.json` lists the "repeat that" phrases per language (Hindi in both scripts); the station adds them to its own list at build time. `{pickup_code}` and `{card_last4}` fill from the station's `code` and `last4`.

Clips are rendered from these files with `python -m ai.render_clips` (Spanish in `carina`, English and Hindi in `ara`) and served by the relay at `/audio/<file>`. The Hindi lines use feminine verb forms for the agent.
