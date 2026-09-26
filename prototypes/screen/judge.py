"""grok judge with a strict JSON schema.

    python judge.py "My grandson is in trouble, buy phones now"            # one line, empty cart
    python judge.py --eval [--model grok-4.20-0309-non-reasoning]           # 10-script eval
    python judge.py --eval --both                                           # time both models
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "ai" / "prompts"
JUDGE_SCHEMA = json.loads((PROMPTS / "judge_schema.json").read_text())
JUDGE_SYSTEM = (PROMPTS / "judge_system.md").read_text()
JUDGE_MODEL = "grok-4.7"
FAST_MODEL = "grok-4.20-0309-non-reasoning"
MANDATE = {"per_purchase_cap": 60, "monthly_cap": 400, "approval_threshold": 40, "currency": "USD"}

load_dotenv(ROOT / ".env")
client = OpenAI(base_url="https://api.x.ai/v1", api_key=os.environ.get("XAI_API_KEY"), timeout=20)


def judge(text: str, cart: list[dict] | None = None, model: str = JUDGE_MODEL) -> tuple[dict, float]:
    user = json.dumps({"shopper_said": text, "cart": cart or [], "mandate": MANDATE}, ensure_ascii=False)
    kwargs = dict(
        model=model,
        temperature=0,
        messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "scam_judgment", "schema": JUDGE_SCHEMA, "strict": True},
        },
    )
    if "non-reasoning" not in model:
        kwargs["reasoning_effort"] = "low"
    start = time.perf_counter()
    resp = client.chat.completions.create(**kwargs)
    ms = (time.perf_counter() - start) * 1000
    out = json.loads(resp.choices[0].message.content)
    validate(out)
    return out, ms


def validate(out: dict) -> None:
    props = JUDGE_SCHEMA["properties"]
    assert set(out) == set(JUDGE_SCHEMA["required"]), f"keys {sorted(out)}"
    assert isinstance(out["scam_score"], (int, float)) and 0 <= out["scam_score"] <= 1, out["scam_score"]
    assert all(p in props["patterns"]["items"]["enum"] for p in out["patterns"]), out["patterns"]
    assert out["action"] in props["action"]["enum"], out["action"]
    assert isinstance(out["rationale"], str) and out["rationale"]


def run_eval(model: str) -> bool:
    lines = yaml.safe_load((Path(__file__).parent / "lines.yaml").read_text(encoding="utf-8"))["judge"]
    ok, times = 0, []
    print(f"\n== {model}")
    for j in lines:
        try:
            out, ms = judge(j["text"], j["cart"], model)
        except Exception as e:  # invalid JSON, schema miss, API error
            print(f"  FAIL  {j['id']:<28} {type(e).__name__}: {e}")
            continue
        times.append(ms)
        good = out["scam_score"] > 0.6 if j["label"] == "scam" else out["scam_score"] < 0.4
        ok += good
        print(f"  {'ok  ' if good else 'MISS'}  {j['id']:<28} score={out['scam_score']:.2f} "
              f"action={out['action']:<16} {ms:6.0f} ms  {','.join(out['patterns'])}")
    if times:
        print(f"  {ok}/{len(lines)} correct, valid JSON {len(times)}/{len(lines)}, "
              f"median {statistics.median(times):.0f} ms, max {max(times):.0f} ms")
    return ok == len(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="*")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--model", default=JUDGE_MODEL)
    args = ap.parse_args()
    if args.eval:
        results = [run_eval(m) for m in ([JUDGE_MODEL, FAST_MODEL] if args.both else [args.model])]
        raise SystemExit(0 if any(results) else 1)
    out, ms = judge(" ".join(args.text), model=args.model)
    print(json.dumps(out, ensure_ascii=False, indent=2), f"\n{ms:.0f} ms")
