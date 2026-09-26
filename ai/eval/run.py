"""Scam eval: rules alone, then rules plus the judge on each model. Writes ai/eval/RESULTS.md.

    python -m ai.eval.run                       # both models, 3 runs each
    python -m ai.eval.run --rules-only
    python -m ai.eval.run --models grok-4.20-0309-non-reasoning --runs 1

Scam is the positive class. A prediction is `refuse` (scam stopped), `category_block` (rules
refused on a blocked category alone) or `allow`. Both refuse and category_block count as
positive. Benign scripts expected to be `category_block` are scored separately and left out
of precision, recall and false refusals. The judge runs on every script the rules do not
refuse, as /checkout does (R7).

JUDGE_THRESHOLD is picked on one half of the set (stratified by language and label) over a
0.50-0.80 grid. Every row of the results table is scored on the other (held-out) half only,
for every layer, so the rows compare like with like.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from policy import judge as judge_mod
from policy.mandate import DEFAULT_MANDATE
from policy.screen import reset_sessions, screen

HERE = Path(__file__).resolve().parent
LANGS = ["en", "es", "hi", "hi_latn"]
GRID = [round(0.50 + 0.05 * i, 2) for i in range(7)]
MANDATE_SUMMARY = {k: DEFAULT_MANDATE[k] for k in (
    "currency", "per_purchase_cap", "monthly_cap", "approval_threshold", "allowed_categories", "blocked_categories")}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def rules_prediction(script: dict) -> str:
    reset_sessions()
    out = screen(script["text"], None, session_id=f"eval-{script['id']}")
    if out["action"] != "refuse":
        return "allow"
    return "refuse" if set(out["refusal"]["patterns"]) - {"blocked_category"} else "category_block"


def metrics(scripts: list[dict], preds: dict[str, str]) -> dict:
    tp = fp = fn = fr = n_benign = 0
    for s in scripts:
        if s["expected"] == "category_block":
            continue
        positive = preds[s["id"]] != "allow"
        if s["label"] == "scam":
            tp += positive
            fn += not positive
        else:
            n_benign += 1
            fp += positive
            fr += positive
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if tp else 0.0
    lo, hi = wilson(fr, n_benign)
    return {"p": precision, "r": recall, "f1": f1, "tp": tp, "fn": fn, "fp": fp,
            "fr": fr, "n_benign": n_benign, "fr_lo": lo, "fr_hi": hi}


def combined(rule_preds: dict[str, str], scores: dict[str, float], t: float) -> dict[str, str]:
    return {sid: p if p != "allow" else ("refuse" if scores.get(sid, 0.0) >= t else "allow")
            for sid, p in rule_preds.items()}


def split(scripts: list[dict]) -> tuple[list[dict], list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for s in scripts:
        groups[(s["lang"], s["label"])].append(s)
    tune, test = [], []
    for group in groups.values():
        for i, s in enumerate(group):
            (tune if i % 2 == 0 else test).append(s)
    return tune, test


def run_judge(model: str, scripts: list[dict], runs: int, timeout: float, workers: int) -> dict:
    jobs = [(s, r) for s in scripts for r in range(runs)]

    def call(job):
        s, r = job
        try:
            out, meta = judge_mod.judge_with_meta(
                s["text"], s.get("cart"), MANDATE_SUMMARY, "", session_id=f"eval-{model}",
                model=model, timeout=timeout)
            return s["id"], r, out["scam_score"], meta, None
        except judge_mod.JudgeError as e:
            return s["id"], r, None, None, str(e)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(call, jobs))
    scores: dict[str, list[float]] = defaultdict(list)
    latencies, cached, errors = [], [], []
    for sid, _, score, meta, err in results:
        if err:
            errors.append(f"{sid}: {err}")
            continue
        scores[sid].append(score)
        latencies.append(meta["ms"])
        cached.append(meta["cached_tokens"])
    return {"scores": scores, "latencies": latencies, "cached": cached, "errors": errors}


def fmt(x: float) -> str:
    return "n/a" if x != x else f"{x:.2f}"


def table(rows: list[tuple[str, dict]]) -> list[str]:
    out = ["| Layer | Language | Precision | Recall | F1 | Scams caught | False refusals (95% Wilson) |",
           "|---|---|---|---|---|---|---|"]
    for name, m in rows:
        layer, lang = name.split(" / ")
        out.append(f"| {layer} | {lang} | {fmt(m['p'])} | {fmt(m['r'])} | {fmt(m['f1'])} | "
                   f"{m['tp']}/{m['tp'] + m['fn']} | {m['fr']}/{m['n_benign']} "
                   f"({m['fr_lo']:.0%} to {m['fr_hi']:.0%}) |")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=f"{judge_mod.FAST_MODEL},{judge_mod.REASONING_MODEL}")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=20.0, help="seconds per judge call during eval")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel judge calls; above 1 the latency figures are inflated")
    ap.add_argument("--rules-only", action="store_true")
    ap.add_argument("--out", default=str(HERE / "RESULTS.md"))
    args = ap.parse_args()

    scripts = yaml.safe_load((HERE / "scripts.yaml").read_text(encoding="utf-8"))["scripts"]
    rule_preds = {s["id"]: rules_prediction(s) for s in scripts}
    tune, test = split(scripts)
    by_lang = {lang: [s for s in test if s["lang"] == lang] for lang in LANGS}

    rows = [(f"Rules only / {lang}", metrics(sub, rule_preds)) for lang, sub in by_lang.items()]
    rows.append(("Rules only / all", metrics(test, rule_preds)))
    lines = [
        "# Scam eval results",
        "",
        f"Run {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}. {len(scripts)} scripts "
        f"({sum(s['label'] == 'scam' for s in scripts)} scam, {sum(s['label'] == 'benign' for s in scripts)} benign) "
        "in English, Spanish, Hindi (Devanagari) and Hinglish, from `ai/eval/scripts.yaml`. "
        "Scam is the positive class; a refusal or a category block both count as a catch. "
        f"The judge runs on every script the rules do not refuse. The table scores the held-out half only "
        f"({len(test)} scripts; the threshold is tuned on the other {len(tune)}). Small samples: read the Wilson intervals, "
        "not the point estimates. With 0 false refusals out of n, the true rate is only bounded by the upper end.",
        "",
    ]
    cat_rows = [s for s in scripts if s["expected"] == "category_block"]
    cat_ok = sum(rule_preds[s["id"]] == "category_block" for s in cat_rows)
    misses = [s["id"] for s in scripts if s["expected"] != "category_block"
              and (rule_preds[s["id"]] != "allow") != (s["label"] == "scam")]

    model_sections: list[str] = []
    if not args.rules_only:
        for model in [m.strip() for m in args.models.split(",") if m.strip()]:
            print(f"judging with {model} ({args.runs} runs)...", flush=True)
            res = run_judge(model, [s for s in scripts if rule_preds[s["id"]] == "allow"], args.runs, args.timeout, args.workers)
            median = {sid: statistics.median(v) for sid, v in res["scores"].items()}

            def f1_at(t, subset):
                m = metrics(subset, combined({s["id"]: rule_preds[s["id"]] for s in subset}, median, t))
                return (m["f1"], -m["fr"], -abs(t - 0.6))

            t_best = max(GRID, key=lambda t: f1_at(t, tune))
            preds = combined(rule_preds, median, t_best)
            flips = sum(len({v >= t_best for v in vals}) > 1 for vals in res["scores"].values())
            lat = sorted(res["latencies"])
            p90 = lat[min(len(lat) - 1, math.ceil(0.9 * len(lat)) - 1)] if lat else 0
            over = sum(ms > 3000 for ms in lat)
            label = f"Rules + {model}"
            rows += [(f"{label} / {lang}", metrics(sub, preds)) for lang, sub in by_lang.items()]
            rows.append((f"{label} / all", metrics(test, preds)))
            held = metrics(test, preds)
            wrong = [s["id"] for s in scripts if s["expected"] != "category_block"
                     and (preds[s["id"]] != "allow") != (s["label"] == "scam")]
            model_sections += [
                f"### {model}",
                "",
                f"- Threshold picked on the tuning half: **{t_best:.2f}**. Held-out half at that threshold: "
                f"F1 {fmt(held['f1'])}, recall {fmt(held['r'])}, false refusals {held['fr']}/{held['n_benign']} "
                f"(95% Wilson up to {held['fr_hi']:.0%}).",
                f"- Judge calls: {len(lat)} ok, {len(res['errors'])} failed. Latency median "
                f"{statistics.median(lat) if lat else 0:.0f} ms, p90 {p90:.0f} ms; {over} of {len(lat)} over the 3 s timeout "
                f"({args.workers} call(s) at a time).",
                f"- Verdict flips across {args.runs} runs at the chosen threshold: {flips} of {len(res['scores'])} judged scripts.",
                f"- Prompt cache: median cached prompt tokens per call {statistics.median(res['cached']) if res['cached'] else 0:.0f}.",
                f"- Misclassified, full set: {', '.join(wrong) or 'none'}.",
                "",
            ]
            if res["errors"]:
                model_sections += ["Errors:", ""] + [f"- {e}" for e in res["errors"][:10]] + [""]

    lines += ["## Results by layer and language (held-out half)", ""] + table(rows) + [""]
    lines += [
        "## Rules only",
        "",
        f"- Innocent requests for a blocked item (expected category block): {cat_ok}/{len(cat_rows)} blocked as a category.",
        f"- Misclassified by rules alone, full set (the judge covers these): {', '.join(misses) or 'none'}.",
        "",
    ]
    if model_sections:
        lines += ["## Rules plus judge", ""] + model_sections
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
