# Visa probes

Run: `python -m merchant.visa_probes` (our own sandbox merchant `…6462` on `apitest.cybersource.com`;
never the public `testrest` credentials).

## Sat Sep 26, 5:12pm ET

| Probe | Result | Verdict |
|---|---|---|
| Intelligent Commerce `POST /icc/v1/instructions` `{}` | HTTP 400 `{"response":{"rmsg":"Bad Request"}}`, correlation `eb1786b4-7f87-4bdb-8162-a5f79f4a7756` | **Not enabled for us (routed, needs pilot auth).** The path exists: a made-up path (`/zzzz/v1/instructions`, `/icc/v1/nothing-here`) answers 404 "Resource not found". But the gateway turns the call away before any field check, unlike `/pts/v2/payments`, which answers `MISSING_FIELD`. Consistent with the guide: JWT auth, message-level encryption, a token requester id and a relationship id from an account manager. |
| Intelligent Commerce `POST /acp/v1/instructions` `{}` | HTTP 400, same body, correlation `4e18fb06-920b-4315-bf4a-491977f194ef` | Same: the old path is still routed. |
| Decision Manager `POST /risk/v1/decisions` (test card 4111…, $11.49) | HTTP 201 `ACCEPTED`, `riskInformation.score.result` **32**, id `7904571494536769504806`, 368 ms | **Works.** Next: run each agent order through it and show the Visa risk score on the ledger. |
| Visa Transaction Controls (Visa Developer sandbox, project `chaperone-vtc`, two-way SSL) | helloworld 200; enroll test PAN `…0001` → `documentID ctc-vd-…e303`; rules set (global $60, ATM $100, e-commerce $150, gambling blocked, household $200); **$480 at Five Points Drug (MCC 5912) → `shouldDecline: true`, `declineRuleCategory: PCT_GLOBAL`, threshold 60.0**, decision `ctc-vd-b76e9acd-59d4-45f4-a0af-51f72bde140d`, 138 ms; $20 there → `shouldDecline: false` | **Works.** Next: show "Visa VTC: decline" next to our own decision for the $480 drugstore swipe. The decision call needs `pointOfServiceInfo` (`terminalType: POS_TERMINAL`, `deviceLocation: ON_PREMISE`). Run: `python -m merchant.vtc_probe`. |

In one sentence: "Our mandate is in Visa Intelligent Commerce's `mandates[]` shape. The instruction API is routed on our
sandbox but needs pilot credentials (JWT + message-level encryption), so we probe it and say so. Every agent order
gets a live Cybersource Decision Manager score, and the same rules the caregiver signs are mirrored in Visa Transaction
Controls, which declines the $480 drugstore charge in the Visa sandbox."
