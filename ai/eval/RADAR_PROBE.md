# Scam radar probe

Run 2026-09-26 17:01 on `grok-4.20-0309-non-reasoning` with `x_search` (last 30 days) and `web_search` limited to consumer.ftc.gov, ic3.gov, aarp.org, bbb.org, fcc.gov. Responses API, strict JSON schema, sources from the tool citations.

This probe (`ai/radar_probe.py`) has its own prompt and made-up account facts (for example a $0.00 Peachtree Power balance on autopay), so the answers below are not what `/scam-check` says today: the production check (`policy/scamcheck.py`) reads Ruth's real accounts, speaks formally (usted, aap), keeps at most 8 sources, and answers a rule hit without waiting for Grok. See `RADAR_LATENCY.md` for the production path.

| Story | Lang | ms | Verdict | Pattern | Actions | Sources | Cost (USD ticks) |
|---|---|---|---|---|---|---|---|
| grandparent | en | 4810 | scam | Grandparent scam: Fake grandson calls from jail after car accident, voice sounds different, begs not to tell mom, urgent bail money needed. Matches FBI/IC3 and AARP warnings on this exact tactic, including secrecy request and voice excuse. Recent X posts confirm ongoing similar scams targeting elderly with jail/bail after accidents. | call_trusted, do_not_pay | 19 | 602893500 |
| power_company | en | 3151 | scam | utility impersonation demanding immediate gift card payment | hang_up, do_not_pay, call_trusted | 15 | 307906000 |
| tech_support | en | 4239 | scam | tech support refund | hang_up, do_not_pay, call_trusted | 18 | 490731000 |
| grandparent | es | 3955 | scam | grandparent scam | call_trusted, do_not_pay | 20 | 545840500 |
| grandparent | hi | 4541 | scam | grandparent scam | call_trusted, do_not_pay | 15 | 443093500 |

### grandparent (en), 4810 ms

- **say**: This sounds like the common grandparent scam reported by the FBI and AARP. Call your grandson Alex right now at +1-404-555-0187 to check if he's safe.
- **reported recently**: FBI IC3 (2023-2025 alerts), AARP (2025 articles on AI-enhanced versions), recent X posts from Sep 2026 about identical jail-after-accident calls.
- **tool usage**: `{"web_search_calls": 2, "x_search_calls": 2, "x_posts_fetched": 7, "x_users_fetched": 0, "code_interpreter_calls": 0, "file_search_calls": 0, "mcp_calls": 0, "document_search_calls": 0, "image_generation_calls": 0}`
- source: [https://www.aarp.org/podcasts/the-perfect-scam/info-2023/grandparent-scam-fbi-investigation-part-1.html](https://www.aarp.org/podcasts/the-perfect-scam/info-2023/grandparent-scam-fbi-investigation-part-1.html)
- source: [https://www.aarp.org/money/scams-fraud/grandparent/](https://www.aarp.org/money/scams-fraud/grandparent/)
- source: [https://www.aarp.org/money/scams-fraud/grandparent-scams-coronavirus/](https://www.aarp.org/money/scams-fraud/grandparent-scams-coronavirus/)
- source: [https://x.com/i/status/2102747577405907454](https://x.com/i/status/2102747577405907454)
- source: [https://x.com/i/status/2093434866259972584](https://x.com/i/status/2093434866259972584)
- source: [https://x.com/i/status/2102081323166847146](https://x.com/i/status/2102081323166847146)
- source: [https://www.ic3.gov/PSA/2023/PSA231117](https://www.ic3.gov/PSA/2023/PSA231117)
- source: [https://x.com/i/status/2102081648611541400](https://x.com/i/status/2102081648611541400)

### power_company (en), 3151 ms

- **say**: This matches a common scam where callers pretend to be from your power company and demand gift cards to avoid shutoff. FTC and AARP confirm real utilities never require gift cards or same-day payment like this, especially with your $0 balance and autopay.
- **reported recently**: no
- **tool usage**: `{"web_search_calls": 3, "x_search_calls": 1, "x_posts_fetched": 0, "x_users_fetched": 0, "code_interpreter_calls": 0, "file_search_calls": 0, "mcp_calls": 0, "document_search_calls": 0, "image_generation_calls": 0}`
- source: [https://consumer.ftc.gov/consumer-alerts/2025/04/discounted-phone-tv-or-internet-services-if-you-pay-gift-card-no-its-scam](https://consumer.ftc.gov/consumer-alerts/2025/04/discounted-phone-tv-or-internet-services-if-you-pay-gift-card-no-its-scam)
- source: [https://consumer.ftc.gov/articles/scammers-pretend-be-your-utility-company](https://consumer.ftc.gov/articles/scammers-pretend-be-your-utility-company)
- source: [https://www.aarp.org/money/scams-fraud/gift-card-payment/](https://www.aarp.org/money/scams-fraud/gift-card-payment/)
- source: [https://consumer.ftc.gov/consumer-alerts/2024/02/check-out-line-buying-gift-cards-read-avoid-scam](https://consumer.ftc.gov/consumer-alerts/2024/02/check-out-line-buying-gift-cards-read-avoid-scam)
- source: [https://www.ic3.gov/PSA/2026/PSA260917](https://www.ic3.gov/PSA/2026/PSA260917)
- source: [https://www.aarp.org/podcasts/the-perfect-scam/tech-scam-traumatizes-teacher.html](https://www.aarp.org/podcasts/the-perfect-scam/tech-scam-traumatizes-teacher.html)
- source: [https://consumer.ftc.gov/consumer-alerts/2024/07/got-barcode-your-utility-company-make-payment-thats-scam](https://consumer.ftc.gov/consumer-alerts/2024/07/got-barcode-your-utility-company-make-payment-thats-scam)
- source: [https://www.aarp.org/money/scams-fraud/utility/](https://www.aarp.org/money/scams-fraud/utility/)

### tech_support (en), 4239 ms

- **say**: This is a common scam using fake Microsoft virus pop-ups and remote access requests like AnyDesk. FTC and AARP confirm these often involve refund claims for non-existent charges to steal more money.
- **reported recently**: FTC consumer advice and AARP Fraud Watch (Sep 2026)
- **tool usage**: `{"web_search_calls": 2, "x_search_calls": 2, "x_posts_fetched": 5, "x_users_fetched": 0, "code_interpreter_calls": 0, "file_search_calls": 0, "mcp_calls": 0, "document_search_calls": 0, "image_generation_calls": 0}`
- source: [https://consumer.ftc.gov/consumer-alerts/2024/03/new-tech-support-scammers-want-your-life-savings](https://consumer.ftc.gov/consumer-alerts/2024/03/new-tech-support-scammers-want-your-life-savings)
- source: [https://x.com/i/status/2095528956468347112](https://x.com/i/status/2095528956468347112)
- source: [https://consumer.ftc.gov/consumer-alerts/2025/04/seemingly-urgent-security-messages-could-lead-tech-support-scams](https://consumer.ftc.gov/consumer-alerts/2025/04/seemingly-urgent-security-messages-could-lead-tech-support-scams)
- source: [https://consumer.ftc.gov/sites/default/files/articles/pdf/tech-support-scam-infographic-508-v3.pdf](https://consumer.ftc.gov/sites/default/files/articles/pdf/tech-support-scam-infographic-508-v3.pdf)
- source: [https://www.ic3.gov/PSA/2022/PSA221110](https://www.ic3.gov/PSA/2022/PSA221110)
- source: [https://consumer.ftc.gov/articles/how-spot-avoid-and-report-tech-support-scams](https://consumer.ftc.gov/articles/how-spot-avoid-and-report-tech-support-scams)
- source: [https://x.com/i/status/2099708601572991165](https://x.com/i/status/2099708601572991165)
- source: [https://www.aarp.org/podcasts/the-perfect-scam/info-2019/florida-tech-support-scam.html](https://www.aarp.org/podcasts/the-perfect-scam/info-2019/florida-tech-support-scam.html)

### grandparent (es), 3955 ms

- **say**: Esto es una estafa común del 'nieto en la cárcel' que reportan el FBI y la FTC. Llama al número de Alex que tienes guardado para confirmar que está bien.
- **reported recently**: FBI IC3 y FTC advierten de este tipo de llamadas recientes con pérdidas millonarias.
- **tool usage**: `{"web_search_calls": 2, "x_search_calls": 2, "x_posts_fetched": 6, "x_users_fetched": 0, "code_interpreter_calls": 0, "file_search_calls": 0, "mcp_calls": 0, "document_search_calls": 0, "image_generation_calls": 0}`
- source: [https://consumer.ftc.gov/features/pass-it-on/impersonator-scams/grandkid-scams](https://consumer.ftc.gov/features/pass-it-on/impersonator-scams/grandkid-scams)
- source: [https://www.ic3.gov/PSA/2026/PSA260917](https://www.ic3.gov/PSA/2026/PSA260917)
- source: [https://www.aarp.org/money/scams-fraud/retirees-lose-money-grandparent-scam/](https://www.aarp.org/money/scams-fraud/retirees-lose-money-grandparent-scam/)
- source: [https://x.com/i/status/2102747577405907454](https://x.com/i/status/2102747577405907454)
- source: [https://www.aarp.org/podcasts/the-perfect-scam/info-2018/grandparent-scam.html](https://www.aarp.org/podcasts/the-perfect-scam/info-2018/grandparent-scam.html)
- source: [https://x.com/i/status/2103134096717156523](https://x.com/i/status/2103134096717156523)
- source: [https://www.bbb.org/article/scams/16918-bbb-scam-alert-emergency-scams](https://www.bbb.org/article/scams/16918-bbb-scam-alert-emergency-scams)
- source: [https://x.com/i/status/2103106450797314418](https://x.com/i/status/2103106450797314418)

### grandparent (hi), 4541 ms

- **say**: ये पोते एलेक्स का फोन नहीं है, ये आम घोटाला है। भरोसेमंद नंबर +1-404-555-0187 से खुद एलेक्स को कॉल करके पूछें।
- **reported recently**: FBI IC3 और AARP reports (2023-2025)
- **tool usage**: `{"web_search_calls": 2, "x_search_calls": 2, "x_posts_fetched": 4, "x_users_fetched": 0, "code_interpreter_calls": 0, "file_search_calls": 0, "mcp_calls": 0, "document_search_calls": 0, "image_generation_calls": 0}`
- source: [https://consumer.ftc.gov/features/pass-it-on/impersonator-scams/grandkid-scams](https://consumer.ftc.gov/features/pass-it-on/impersonator-scams/grandkid-scams)
- source: [https://www.aarp.org/money/scams-fraud/retirees-lose-money-grandparent-scam/](https://www.aarp.org/money/scams-fraud/retirees-lose-money-grandparent-scam/)
- source: [https://www.ic3.gov/PSA/2023/PSA231117](https://www.ic3.gov/PSA/2023/PSA231117)
- source: [https://www.aarp.org/money/scams-fraud/grandparent/](https://www.aarp.org/money/scams-fraud/grandparent/)
- source: [https://x.com/i/status/2103134096717156523](https://x.com/i/status/2103134096717156523)
- source: [https://www.aarp.org/podcasts/the-perfect-scam/info-2025/scammer-targets-hockey-league-parents-part-two.html](https://www.aarp.org/podcasts/the-perfect-scam/info-2025/scammer-targets-hockey-league-parents-part-two.html)
- source: [https://www.ic3.gov/PSA/2021/PSA210729](https://www.ic3.gov/PSA/2021/PSA210729)
- source: [https://consumer.ftc.gov/consumer-alerts/2021/04/dont-open-your-door-grandparent-scams](https://consumer.ftc.gov/consumer-alerts/2021/04/dont-open-your-door-grandparent-scams)
