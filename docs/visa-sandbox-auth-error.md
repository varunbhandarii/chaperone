# Visa sandbox: card authorization fails with reason 150 (usd_outlet_id, usd_terminal_id)

Status: **open, waiting on Cybersource** · Found: Fri Sep 25 2026, ~9:55pm

## TL;DR

Our Cybersource sandbox account (merchant `…6462`) can create real Visa Pay by Link pages, but **every card authorization fails** with reason code 150:

> The following property is either invalid or missing: usd_outlet_id, usd_terminal_id

This is a known Cybersource sandbox provisioning bug. The account was created without the outlet/terminal IDs the test processor (`fdiglobal`) needs. It is not our code, the request, or the test card, and it cannot be fixed from the Business Center. Only Cybersource staff can fix it.

Demo impact: none. Real links and the real Visa checkout page work. Until the account is fixed, the Host marks orders paid via `POST /orders/{id}/paid`.

## What works and what doesn't

| Step | Status | Evidence |
|---|---|---|
| Credentials / HTTP Signature auth | ✅ | MCP and REST calls accepted |
| Create Pay by Link (`create_payment_link` via `@visaacceptance/mcp`) | ✅ | Links on `ebc2test.cybersource.com/ebc2/payByLink/pay/...` |
| Hosted checkout page, test card entry | ✅ | Visa 411111XXXXXX1111 recognized, amount 11.49 |
| Decision Manager (fraud screen) | ✅ | "Early Success" |
| **Card Authorization** | ❌ | Reason 150, reply code -1, "Processing Error" |
| Card Settlement | ⏸ | "Not Run" (because auth failed) |

## Failing transactions

| Request ID | Path | Amount | Result |
|---|---|---|---|
| 7903878036966362404008 | Pay by Link hosted page | 11.49 USD | 150 ESYSTEM usd_outlet_id, usd_terminal_id |
| 7903886322116330804009 | Pay by Link hosted page (after line-item fix) | 11.49 USD | same |
| 7903881811666223704806 | Direct REST `POST /pts/v2/payments` | 1.00 USD | same |
| 7903885492076530204805 | Direct REST retry | 1.00 USD | same |

Business Center transaction details for these show: Client App PayByLink 2.1 (user UC), processor `fdiglobal`, commerce indicator `internet`, reply code `-1`, and "AI Transaction Insights: explanation not yet available".

## Plain-language explanation

A card payment has to ask the bank for approval ("authorization"). To do that, the merchant account needs ID numbers that tell the bank which store and terminal is asking. Cybersource created our sandbox account without those IDs (`usd_outlet_id`, `usd_terminal_id`), so the request is dropped **before any bank or card check happens**. Reply code `-1` confirms this: a real decline returns a 2xx reason code (e.g. 203, 204).

Using a real card would not help, because the failure happens before the card is looked at. Real card data is also not allowed in the sandbox ("only simulated Test Data may be submitted").

## Root cause: confirmed by isolation

We ran the same $1.00 REST authorization with the same code and test card against Cybersource's **public sample merchant** (`testrest`, credentials published in [CyberSource/cybersource-rest-samples-python](https://github.com/CyberSource/cybersource-rest-samples-python/blob/master/data/Configuration.py)):

| Merchant | REST auth | Pay by Link |
|---|---|---|
| `…6462` (ours) | ❌ reason 150 | ✅ link created |
| `testrest` (Cybersource public sample) | ✅ **AUTHORIZED** (request 7903891460546311804806) | ❌ not enabled on that account |

Same code and same card, and the only difference is the merchant account. So the fault is our account's provisioning.

## What the internet says

We read all 19 Cybersource Developer Community threads on this error (2022 to Sep 16 2026):

- The error is widespread on new sandbox accounts, always with processor `fdiglobal` and reason 150. Reports continue through 2026: Mar (x3), Apr, May, Jun, Jul, Sep 16.
- There is no self-service fix. Users could not find any "merchant settings" for it in the Business Center, and the forum replies saying "add the IDs to your request" never worked for anyone.
- The only working fix is Cybersource staff provisioning the account manually:
  - 2022: a Cybersource engineer fixed accounts per merchant ID in-thread, and posters confirmed it worked.
  - 2024: "Reach out to developer@cybersource.com and give them your merchant id. They were able to fix the issue for me."
  - 2025: one ticket was closed as "resolved" without being fixed. Verify before trusting a "resolved".
- A new sandbox account is unlikely to help. From Jul 2026: "I'm getting the same issue with a new account… The CyberSource Sandbox has had major issues for weeks now."

Sources: [usd_outlet_id/usd_terminal_id thread (2026)](https://community.developer.cybersource.com/t5/cybersource-APIs/The-following-property-is-either-invalid-or-missing-usd-outlet/m-p/95988) · [Reason 150 on sandbox (Jul 2026)](https://community.developer.cybersource.com/t5/cybersource-APIs/Sandbox-Payment-Authorization-Fails-with-Reason-Code-150-Missing/td-p/95585) · [Fixed via developer@cybersource.com (2024)](https://community.developer.cybersource.com/t5/cybersource-APIs/ERROR-Invalid-or-missing-prop-merchant-category-code-usd-outlet/td-p/90331) · [Staff fix confirmed (2022)](https://community.developer.cybersource.com/t5/cybersource-APIs/Problem-when-making-Credit-Card-Authorization-on-Sandbox-Account/td-p/83690)

## Actions

- [ ] Ask Visa developer support to provision the account, or for a pre-configured sandbox. Have ready: merchant ID, request ID 7903886322116330804009, the reply message, processor fdiglobal.
- [ ] Email developer@cybersource.com (or use **Get help** on the transaction's details page in the Business Center) with the merchant ID and request IDs above.
- [ ] After any "fixed" reply, verify: `.venv/bin/python -m merchant.cybs_check` must print `AUTHORIZED`.
- [ ] Then pay a fresh link (`.venv/bin/python -m merchant.spike_link`) with 4111 1111 1111 1111, 12/30, 123 and confirm Card Authorization turns green in Transaction Management.
- [ ] Deadline Sat 6pm: if still broken, demo uses the real link and checkout page, and the Host marks paid via `POST /orders/{id}/paid`.
- [ ] Optional backup (only if support can't help): charge a saved test card via REST `POST /pts/v2/payments` on the public `testrest` merchant, which gives a real AUTHORIZED response. Disclose that it is Cybersource's shared sample merchant.

## Email draft

> **To:** developer@cybersource.com
> **Subject:** Sandbox auth fails, reason 150: usd_outlet_id, usd_terminal_id missing (merchant …6462)
>
> Hi, my new sandbox account can't authorize test cards. Every authorization, both Pay by Link and REST `/pts/v2/payments`, fails with reason code 150 ESYSTEM: "The following property is either invalid or missing: usd_outlet_id, usd_terminal_id". Processor: fdiglobal.
>
> Merchant ID: …6462
> Request IDs: 7903878036966362404008, 7903886322116330804009, 7903881811666223704806
>
> Could you please provision the USD outlet and terminal IDs for this sandbox account? We're using it for a prototype. Thank you!

## Tools

- `merchant/cybs_check.py`: `python -m merchant.cybs_check` runs a $1.00 test authorization and prints `AUTHORIZED` or the exact reason. `python -m merchant.cybs_check <request_id>` explains any transaction ID from Transaction Management.
- `merchant/spike_link.py`: creates a real $11.49 demo link through the merchant service (`--direct` makes a $1.00 link straight through the MCP).

## Related gotchas found along the way (already handled in `merchant/visa.py`)

- **Pay by Link charges only the first line item.** A 2-item $11.49 cart became an $8.00 link, so we send the cart as one line ("Corner Market order (N items)") and reject any link whose total differs from ours.
- **A `;` in a line-item description fails the whole request** with "Failed to create payment link", so descriptions are sanitized.
- **`@visaacceptance/mcp` 0.0.96 truncates `--secret-key=` values containing `=`**, so creds go in env vars.
- **The MCP calls production unless `VISA_ACCEPTANCE_ENVIRONMENT=SANDBOX` is set**, so we always set it.
- **The hosted checkout page shows the merchant contact details from signup.** Update the phone and address in the Business Center before the demo; it currently shows "undefined," as the address line.
