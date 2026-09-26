"""Run the "Why did it refuse?" fixtures on the real model and check tone. Writes ai/eval/EXPLAIN.md.

    python -m ai.eval.explain_check

Checks per case: an answer within the 3 s deadline, all five fields, no rule ids, scores or field
names, no alarming words, one sentence and at most 30 words per field. The table is for a human
tone read as well.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import yaml

from ai.explain import FIELDS, ExplainError, explain, problems
from common.config import env

HERE = Path(__file__).resolve().parent


def main() -> None:
    if env("EXPLAIN_FAKE", "0") == "1":
        raise SystemExit("EXPLAIN_FAKE=1: the fixed answer is not evidence; unset it and rerun")
    spec = yaml.safe_load((HERE / "explain_cases.yaml").read_text(encoding="utf-8"))
    out = ["# \"Why did it refuse?\" check", "",
           f"Run {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} on {env('EXPLAIN_MODEL', 'grok-4.20-0309-non-reasoning')}, "
           "prompt `ai/prompts/explain_system.md`, cases `ai/eval/explain_cases.yaml`.", ""]
    ok = 0
    for case in spec["cases"]:
        start = time.perf_counter()
        try:
            answer = explain(case["decision"], ruth_said=case["ruth_said"], screen_hits=case["screen_hits"],
                             limits=spec["limits"])
            issues = problems(answer)
        except ExplainError as e:
            answer, issues = {}, [str(e)]
        ms = (time.perf_counter() - start) * 1000
        ok += not issues
        out += [f"## {case['id']} ({ms:.0f} ms): {'ok' if not issues else 'CHECK'}", "",
                f"Ruth said: \"{case['ruth_said']}\"", ""]
        out += [f"- **{f}**: {answer.get(f, '')}" for f in FIELDS]
        out += [f"- Problem: {p}" for p in issues] + [""]
        print(f"{case['id']:18} {ms:5.0f} ms  {'ok' if not issues else issues}")
    out.insert(3, f"{ok} of {len(spec['cases'])} cases pass the checks.\n")
    (HERE / "EXPLAIN.md").write_text("\n".join(out), encoding="utf-8")
    raise SystemExit(0 if ok == len(spec["cases"]) else 1)


if __name__ == "__main__":
    main()
