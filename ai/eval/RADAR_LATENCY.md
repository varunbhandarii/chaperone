# Scam radar latency and cost

Run 2026-09-26 21:20: 10 full `/scam-check` runs where Grok answers (rules, account facts, then Grok with X and web search), one after another, against temporary files.

- **Latency:** median 3854 ms, p90 5251 ms, max 5487 ms (budget 11 s; the station and the line wait up to 14 s).
- **Cost per check:** median $0.046, max $0.092 (10 calls; xAI's `cost_in_usd_ticks`, 1 USD = 10^10 ticks). These are checks the rules don't settle, so Grok answers Ruth; every such check calls Grok, and the cache is only the fallback when Grok is late or fails.
- **A rule hit is not free:** Ruth's answer doesn't wait for Grok, but unless the story, or an earlier scam check of the same pattern in the same language, has cached sources, one Grok call with the same X and web search still runs in the background (up to `RADAR_TIMEOUT_S`, 12 s) to attach sources to Priyank's alert, at about the cost of a check above. Once it returns a scam verdict with sources, later hits of that pattern and language reuse them and make no call. With `RADAR_FAKE=1` no call is made.

| Lang | Story | Verdict | ms | Sources | From |
|---|---|---|---|---|---|
| en | Someone from Social Security called and says my number was used in a c | scam | 3349 | 8 | Grok |
| en | A man called saying he's from my bank's fraud team and my account has  | scam | 4427 | 8 | Grok |
| en | I got a text that my Amazon order was charged twice and I should call  | scam | 4420 | 8 | Grok |
| en | A woman called and said I won a sweepstakes, but I have to cover the t | scam | 2865 | 8 | Grok |
| es | Me llamó un hombre del Seguro Social, dice que mi número está suspendi | scam | 3422 | 8 | Grok |
| es | Recibí un mensaje de que mi paquete no se pudo entregar y tengo que co | scam | 3553 | 8 | Grok |
| hi | किसी ने फोन करके कहा कि मेरा बैंक खाता बंद होने वाला है और मुझे अपनी ज | scam | 3960 | 8 | Grok |
| hi | एक आदमी का फोन आया, बोला कि मेरे नाम पर एक पार्सल में गैरकानूनी सामान  | scam | 5251 | 8 | Grok |
| hi | Ek aadmi ne phone karke kaha ki meri bijli ka bill galat hai aur use t | scam | 3747 | 8 | Grok |
| en | My neighbor's son says he can double my savings if I invest with his f | scam | 5487 | 8 | Grok |
