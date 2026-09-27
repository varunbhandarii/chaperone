"""Radar latency and cost: 10 full /scam-check runs where Grok answers (stories the rules don't refuse on
their own), end to end: rules, facts, then Grok with X and web search. Writes ai/eval/RADAR_LATENCY.md.

    python -m ai.eval.radar_latency

Runs against temporary files, so Ruth's real cool-down and checks are untouched. Cost comes from xAI's
usage.cost_in_usd_ticks (1 USD = 10^10 ticks).
"""

from __future__ import annotations

import datetime as dt
import math
import os
import statistics
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_TMP = tempfile.mkdtemp(prefix="radar_latency_")
for var, name in (("RISK_PATH", "risk.json"), ("RADAR_CACHE_PATH", "cache.json"), ("SCAM_CHECKS_PATH", "checks.json"),
                  ("DECISIONS_PATH", "decisions.json")):
    os.environ[var] = str(Path(_TMP) / name)
os.environ["RELAY_URL"] = ""

from policy import scamcheck  # noqa: E402
from policy.screen import screen  # noqa: E402

TICKS_PER_USD = 10_000_000_000
STORIES = [
    ("en", "Someone from Social Security called and says my number was used in a crime and I need to verify it today."),
    ("en", "A man called saying he's from my bank's fraud team and my account has suspicious activity."),
    ("en", "I got a text that my Amazon order was charged twice and I should call a number to fix it."),
    ("en", "A woman called and said I won a sweepstakes, but I have to cover the taxes first."),
    ("es", "Me llamó un hombre del Seguro Social, dice que mi número está suspendido y tengo que confirmarlo hoy."),
    ("es", "Recibí un mensaje de que mi paquete no se pudo entregar y tengo que confirmar mi dirección en un enlace."),
    ("hi", "किसी ने फोन करके कहा कि मेरा बैंक खाता बंद होने वाला है और मुझे अपनी जानकारी देनी होगी।"),
    ("hi", "एक आदमी का फोन आया, बोला कि मेरे नाम पर एक पार्सल में गैरकानूनी सामान मिला है।"),
    ("hi", "Ek aadmi ne phone karke kaha ki meri bijli ka bill galat hai aur use theek karna hai."),
    ("en", "My neighbor's son says he can double my savings if I invest with his friend this week."),
]


def main() -> None:
    costs: list[int] = []
    real_radar = scamcheck.radar

    def counting_radar(*args, **kwargs):
        v = real_radar(*args, **kwargs)
        if v.get("cost_usd_ticks"):
            costs.append(int(v["cost_usd_ticks"]))
        return v

    scamcheck.radar = counting_radar
    rows, times = [], []
    for lang, story in STORIES:
        if screen(story, lang)["action"] == "refuse":
            rows.append(f"| {lang} | {story[:70]} | skipped (a rule refuses it; Grok would not run) | | | |")
            continue
        out = scamcheck.check(story, lang)
        times.append(out["ms"])
        rows.append(f"| {lang} | {story[:70]} | {out['verdict']} | {out['ms']} | {len(out['sources'])} | "
                    f"{'cache' if out['from_cache'] else 'Grok'} |")
        print(f"{lang} {out['ms']:6} ms  {out['verdict']:6} sources={len(out['sources'])}  {story[:50]}")
    times.sort()
    p90 = times[min(len(times) - 1, math.ceil(0.9 * len(times)) - 1)] if times else 0
    usd = [c / TICKS_PER_USD for c in costs]
    lines = [
        "# Scam radar latency and cost", "",
        f"Run {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}: {len(times)} full `/scam-check` runs where Grok answers "
        "(rules, account facts, then Grok with X and web search), one after another, against temporary files.", "",
        f"- **Latency:** median {statistics.median(times) if times else 0:.0f} ms, p90 {p90} ms, max {max(times) if times else 0} ms "
        f"(budget 11 s; the station and the line wait up to 14 s).",
        f"- **Cost per check:** median ${statistics.median(usd) if usd else 0:.3f}, max ${max(usd) if usd else 0:.3f} "
        f"({len(usd)} calls; xAI's `cost_in_usd_ticks`, 1 USD = 10^10 ticks). A rule hit or a cached story costs nothing.",
        "", "| Lang | Story | Verdict | ms | Sources | From |", "|---|---|---|---|---|---|", *rows, "",
    ]
    (HERE / "RADAR_LATENCY.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[3:6]))


if __name__ == "__main__":
    main()
