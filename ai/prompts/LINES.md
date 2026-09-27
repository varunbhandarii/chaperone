# Spoken lines

`lines.<lang>.json` (en, es, hi) is the single source for every fixed line the station and the phone line speak, and for the refusal text `/screen` returns. The station registers them over its built-in defaults. The three files have the same keys and the same `{placeholders}`; a test checks both. No store name is hard-coded: stores come in as `{store}` or `{biller}`.

| Key | Placeholders | When |
|---|---|---|
| `ordering_now` | `{total}` | checkout allowed |
| `asking_priya` | | over the approval threshold; clip `line.asking_priya` |
| `caregiver_approved` | `{total}` | Priyank approved |
| `caregiver_declined`, `caregiver_timeout` | | Priyank declined, or no answer |
| `receipt_done`, `receipt_on_screen` | `{total}`, `{store}`, `{pickup}` | receipt printed or on screen; clip `line.receipt_done` from `receipt_done_clip` (no amount or store) |
| `pickup_line` | `{code}` | fills `{pickup}` for store orders; `{pickup}` is empty for a bill (collapse the double space) |
| `order_ready` | `{store}`, `{code}` | the order is ready |
| `order_status`, `order_status_pickup` | `{status}`, `{code}` | "where is my order?" |
| `no_orders` | | no order today |
| `order_cancelled`, `cancel_too_late` | | cancel on an unpaid or a paid order |
| `refund_preview`, `refund_done` | `{amount}`, `{last4}` | return read-back, then done; clip `line.refund_done` from `refund_done_clip` |
| `refund_not_allowed_rx`, `refund_not_allowed_bill`, `refund_not_possible` | `{biller}` (bill only) | a prescription, a bill, or anything else that can't be returned |
| `you_saved`, `loyalty_points` | `{saved}`, `{points}` | after the receipt line; leave `you_saved` out when nothing was saved |
| `bill_due`, `bill_past_due`, `bill_paid` | `{biller}`, `{amount}`, `{due}` | the bill status |
| `history_summary`, `history_summary_one`, `history_last`, `history_none` | `{count}`, `{days}`, `{spent}`, `{items}` | "what did I buy?" |
| `card_declined_blocked`, `card_declined_cooldown`, `card_declined_over_cap`, `card_declined_unusual`, `card_declined_atm` | `{amount}`, `{store}` | the card guard declined a swipe; clips `line.card_declined_blocked` and `line.card_declined_cooldown` from the `*_clip` lines |
| `card_allowed_once` | | Priyank allowed a held swipe once; clip `line.card_allowed_once` |
| `cooldown_on` | | after a scam check puts the card on its 24-hour cool-down |
| `scam_check_scam`, `scam_check_unsure`, `scam_check_ok` | | `/scam-check`'s fixed lines when Grok has no answer; clip `line.scam_check_scam` |
| `scam_check_bill_paid`, `scam_check_family` | `{biller}`, `{name}` | `/scam-check`'s fixed scam line when her own accounts answer the story: the bill is paid, or the relative has a number on file |
| `scam_check_unavailable` | | the scam check didn't answer at all |
| `line_pin_ask`, `line_pin_ok` | | the phone line's PIN before any purchase |
| `line_pin_wrong`, `line_pin_wrong_last`, `line_pin_locked` | `{left}`, `{minutes}` | a wrong PIN, the last try, then the pause |
| `asking_priya_check` | | the safety check couldn't finish, so the order went to Priyank |
| `cosign_ask`, `cosign_thanks`, `cosign_not_yet` | `{rules}` (ask only) | Ruth agrees to the rules by voice; `{rules}` is the station's plain-words summary in Ruth's language |
| `checkout_unavailable`, `store_unavailable`, `over_monthly_cap`, `read_back_required`, `declined`, `cart_empty` | | checkout and store outcomes |
| `budget_left` | `{left}` | budget question |
| `agent_paused` | | the mandate is paused |
| `repeat_nothing` | | "repeat that" before anything was said |
| `blocked_category`, `scam_pattern`, `code_reading`, `refund_scam` | | refusals; clips `refusal.<key>` |

`repeat_triggers.json` lists the "repeat that" phrases per language (Hindi in both scripts).

Clips are rendered from these files with `python -m ai.render_clips` (Spanish in `carina`, English and Hindi in `ara`) and served by the relay at `/audio/<file>`. The Hindi lines use feminine verb forms for the agent.
