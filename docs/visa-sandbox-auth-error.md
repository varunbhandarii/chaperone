# Visa sandbox: card authorization fails with reason 150 (usd_outlet_id, usd_terminal_id)

Status: **closed: won't fix, the demo keeps the mock paid step** · Found: Fri Sep 25 2026, ~9:55pm

**Decision (Sat Sep 26, ~2:50pm):** a Visa mentor told us not to pursue the processor fix and to keep the mock
payment step. The demo uses real Pay by Link pages at the right total (confirmed on the hosted page: $49.95 for
five Ensure, $11.49 for medicine and bread), a real `INACTIVE` on cancel, and the Host's **Confirm payment** for
the paid step ("marked paid in the sandbox flow"). Don't retry card payments on the hosted page; every attempt
fails the same way. Still failing as of Sep 26 18:34 GMT: request `7904476728146353004011` ($49.95, Pay by Link)
and `7904329513516873604807` (14:29 GMT), both reason 150, `fdiglobal`, `usd_outlet_id, usd_terminal_id`.

## TL;DR

Our Cybersource sandbox account (merchant `…6462`) can create real Visa Pay by Link pages, but **every card authorization fails** with reason code 150:

> The following property is either invalid or missing: usd_outlet_id, usd_terminal_id

This is a known Cybersource sandbox provisioning bug. The account was created without the outlet/terminal IDs the test processor (`fdiglobal`) needs. It is not our code, the request, or the test card, and it cannot be fixed from the Business Center. Only Cybersource staff can fix it.

Demo impact: none. Real links and the real Visa checkout page work. Until the account is fixed, the Host marks orders paid with Confirm payment on the Host page (or `POST /orders/{id}/paid` with `X-Chaperone-Host: 1`).

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

## Cybersource's definition of reason 150, and what it means for us

> System error. … Depending on which payment processor is handling the transaction, the error might indicate a valid Cybersource system error, or it might indicate a processor rejection because of invalid data. In either case, do not design your system to endlessly try to resend a transaction when a system error occurs.

- **Which kind is ours?** The reply message names two merchant configuration properties, not request fields, and the identical request authorizes on the `testrest` merchant. So this is account provisioning, not invalid data. It is also not transient: 4 failures over 30 minutes, and forum accounts stayed broken for days.
- **No endless retries (we comply):** the merchant never sends authorizations itself; the hosted page sends one per Pay click. `cybs_check` makes one authorization per run and only polls the read-only transaction lookup, for up to 30 s. Link creation does not retry; on failure it falls back once to the mock and records `visa_last_error`.
- **Rule for future payment detection:** treat 150 as terminal for that attempt. Surface it to the Host ("payment system error: mark paid or retry once") and never auto-resend in a loop.

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
- [ ] Deadline Sat 6pm: if still broken, demo uses the real link and checkout page, and the Host marks paid with Confirm payment on the Host page.
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

## Update, Fri 11pm: what else we checked, and a checkout that is ready the moment the account is fixed

Re-tested at 10:45pm: still reason 150 on `fdiglobal` (request 7903904563506716104807), from a second laptop, so it is not machine-specific.

**It is a provisioning fault, and there is no self-service fix.** Cybersource's own article says "Sandbox accounts default to Chase Paymentech. To configure a different processor, submit a Support case" ([KA-07420](https://support.visaacceptance.com/knowledgebase/knowledgearticle/?code=KA-07420)). Our account came up on `fdiglobal` without the outlet and terminal IDs, which is the fault. Self-service processor editing exists only for portfolio accounts ([000003120](https://support.visaacceptance.com/knowledgebase/knowledgearticle/?code=000003120)). The request fields people try, `pointOfSaleInformation.terminalId` and `processingInformation.processorId`, are values issued by the processor or Support, so they cannot fix it.

**The fastest official route is a support case.** In the Test Business Center: Support (top right), Support Center, Support Cases, **MID Configuration Request**, then Processor Configuration, Test ([000002638](https://support.visaacceptance.com/knowledgebase/article/000002638/en-us)); if that menu is missing in ebc2test, use the email below. The stated response time is 1 to 2 business days ([contact](https://developer.cybersource.com/support/contact-us.html)), and fixes have ranged from next day to a week, so a same-day fix is unlikely. Phone: developer support 1-800-530-9095, client services 1-800-709-7779.

Case text:

> Merchant ID …6462 (sandbox). All card authorizations fail with reason 150 ESYSTEM "The following property is either invalid or missing: usd_outlet_id, usd_terminal_id", processor fdiglobal, both Pay by Link and REST /pts/v2/payments. Request IDs 7903886322116330804009, 7903904563506716104807. KA-07420 says sandbox accounts default to Chase Paymentech; please configure the test processor (or provision the fdiglobal outlet and terminal IDs) for this merchant.

**A fresh sandbox is a long shot** (new accounts failed the same way through Sep 2026), and the Visa Acceptance and Intelligent Commerce sandbox sign-ups land in the same Test Business Center with no evidence of a different processor.

**What is now in the code: `CARD_AUTH=1`.** Orders get a second URL, `checkout_url` = `/checkout/<order_id>`: our own checkout page (large type, prefilled with the Visa test card) that sends one real `POST /pts/v2/payments` authorization per click and marks the order paid only when the response is `AUTHORIZED`, recording the request id and approval code on the order and the ledger (`card_authorized`, or `card_auth_failed` with the reason). It never retries, and it refuses non-test card numbers before any request. Today it shows "Payment system error, the Host can mark the order paid" because of reason 150; **the minute Cybersource fixes the account it shows a real authorization with no code change.** It authorizes on our merchant by default; `CARD_AUTH_MERCHANT_ID/_API_KEY_ID/_SECRET_KEY` can point it at another sandbox merchant the team owns. The Pay by Link flow is unchanged, and `CARD_AUTH` is off by default.

- Verify after a fix: `python -m merchant.card_auth` must print `"status": "AUTHORIZED"`.
- Tests: `merchant/tests/test_card_auth.py` (authorized marks paid, failure stays unpaid with one attempt, double click never re-authorizes, non-test cards refused, override precedence).

**Also fixed:** every catalog read and write now passes `encoding="utf-8"`. On Windows the default codec is cp1252 and `Catalog.load()` crashed on the catalog JSON, so the merchant and catalog services could not start on a Windows laptop.

## Tools

- `merchant/cybs_check.py`: `python -m merchant.cybs_check` runs a $1.00 test authorization and prints `AUTHORIZED` or the exact reason. `python -m merchant.cybs_check <request_id>` explains any transaction ID from Transaction Management.
- `merchant/spike_link.py`: creates a real $11.49 demo link through the merchant service (`--direct` makes a $1.00 link straight through the MCP).

## Related gotchas found along the way (already handled in `merchant/visa.py`)

- **Pay by Link prices one unit of the first line.** A 2-item $11.49 cart became an $8.00 link, and one line of 5 x $9.99 became $9.99. Every cart now goes up as one line, quantity 1 at the cart total ("Corner Market order (N items)" or "5 x Ensure ..."), and a link whose created or stored total differs from ours is deactivated and rejected.
- **A `;` in a line-item description fails the whole request** with "Failed to create payment link", so descriptions are sanitized.
- **`@visaacceptance/mcp` 0.0.96 truncates `--secret-key=` values containing `=`**, so creds go in env vars.
- **The MCP calls production unless `VISA_ACCEPTANCE_ENVIRONMENT=SANDBOX` is set**, so we always set it.
- **The hosted checkout page shows the merchant contact details from signup.** Update the phone and address in the Business Center before the demo; it currently shows "undefined," as the address line.
