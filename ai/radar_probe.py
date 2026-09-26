"""Scam radar probe: one Grok Responses call per story, with X search and web search, returning a
cited, structured verdict. Records time, sources and cost per call in ai/eval/RADAR_PROBE.md.

    python -m ai.radar_probe                      # the three stories in English, grandparent in es and hi
    python -m ai.radar_probe --model grok-4.3 --effort low

The request: the Responses API with x_search and web_search, a strict JSON schema, sources from the citations.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import httpx

from common.config import env

HERE = Path(__file__).resolve().parent
URL = "https://api.x.ai/v1/responses"
DOMAINS = ["consumer.ftc.gov", "ic3.gov", "aarp.org", "bbb.org", "fcc.gov"]
INSTRUCTIONS = (
    "You protect an older adult from scams. Use the facts given and search X (last 30 days) and the web. "
    "Reply only in the schema. 'say' is in the shopper's language, two short sentences, warm, never a score, "
    "one clear action. Never blame her. Mention only sources the tools returned."
)
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "pattern", "say", "actions", "reported_recently"],
    "properties": {
        "verdict": {"type": "string", "enum": ["scam", "unsure", "ok"]},
        "pattern": {"type": "string"},
        "say": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "string", "enum": [
            "hang_up", "call_priya", "call_trusted", "do_not_pay", "none"]}},
        "reported_recently": {"type": "string"},
    },
}
STORIES = [
    ("grandparent", "en",
     "My grandson Alex just called crying. He's in jail after a car accident and needs $2,000 for bail today. "
     "His voice sounded a little different. He begged me not to tell his mom.",
     "Trusted contacts on file: Alex (grandson) +1-404-555-0187. Caller number: unknown."),
    ("power_company", "en",
     "Peachtree Power called and said my power will be cut off tonight unless I pay $480 right away with gift cards.",
     "Peachtree Power account: balance $0.00, last bill $96.20 paid on Sep 12, autopay on."),
    ("tech_support", "en",
     "A pop-up said my computer has a virus and gave a Microsoft number. The man says I was overcharged and "
     "needs me to install AnyDesk so he can refund me.",
     "No recent Microsoft orders. Recent orders: groceries on Sep 24."),
    ("grandparent", "es",
     "Mi nieto Alex llamó llorando, está en la cárcel y necesita 2000 dólares para la fianza hoy. "
     "Me pidió que no le diga a su mamá.",
     "Contactos de confianza: Alex (nieto) +1-404-555-0187."),
    ("grandparent", "hi",
     "मेरे पोते एलेक्स का फोन आया, वो रो रहा था। वो जेल में है और आज ज़मानत के लिए 2000 डॉलर चाहिए। "
     "उसने कहा अपनी मम्मी को मत बताना।",
     "भरोसेमंद संपर्क: एलेक्स (पोता) +1-404-555-0187."),
]


def request_body(story: str, lang: str, facts: str, model: str, effort: str | None, today: dt.date) -> dict:
    body = {
        "model": model,
        "instructions": INSTRUCTIONS,
        "input": [{"role": "user", "content": f"Language: {lang}. Story: {story} Facts: {facts}"}],
        "tools": [
            {"type": "x_search", "from_date": (today - dt.timedelta(days=30)).isoformat(), "to_date": today.isoformat()},
            {"type": "web_search", "filters": {"allowed_domains": DOMAINS}},
        ],
        "max_turns": 3,
        "include": ["no_inline_citations"],
        "text": {"format": {"type": "json_schema", "name": "scam_verdict", "strict": True, "schema": SCHEMA}},
    }
    if effort:
        body["reasoning"] = {"effort": effort}
    return body


def parse(resp: dict) -> tuple[dict | None, list[dict], dict]:
    """(verdict, sources, usage) from a Responses API reply."""
    verdict, sources = None, []
    for item in resp.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") != "output_text":
                continue
            try:
                verdict = json.loads(part.get("text") or "")
            except json.JSONDecodeError:
                verdict = {"unparsed": part.get("text")}
            for ann in part.get("annotations") or []:
                if ann.get("type") == "url_citation" and ann.get("url"):
                    sources.append({"title": ann.get("title") or "", "url": ann["url"]})
    return verdict, list({s["url"]: s for s in sources}.values()), resp.get("usage") or {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="grok-4.20-0309-non-reasoning")
    ap.add_argument("--effort", default=None, help="reasoning effort for reasoning models")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    today = dt.date.today()
    headers = {"Authorization": f"Bearer {env('XAI_API_KEY')}"}
    out = ["# Scam radar probe", "",
           f"Run {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} on `{args.model}` with `x_search` (last 30 days) and "
           f"`web_search` limited to {', '.join(DOMAINS)}. Responses API, strict JSON schema, sources from the tool citations.", "",
           "| Story | Lang | ms | Verdict | Pattern | Actions | Sources | Cost (USD ticks) |", "|---|---|---|---|---|---|---|---|"]
    details = []
    for name, lang, story, facts in STORIES:
        start = time.perf_counter()
        try:
            r = httpx.post(URL, headers=headers, json=request_body(story, lang, facts, args.model, args.effort, today),
                           timeout=args.timeout)
            ms = (time.perf_counter() - start) * 1000
            if r.status_code != 200:
                out.append(f"| {name} | {lang} | {ms:.0f} | HTTP {r.status_code} | | | | |")
                details += [f"### {name} ({lang}): HTTP {r.status_code}", "", "```", r.text[:800], "```", ""]
                print(name, lang, r.status_code, r.text[:300])
                continue
            verdict, sources, usage = parse(r.json())
        except httpx.HTTPError as e:
            ms = (time.perf_counter() - start) * 1000
            out.append(f"| {name} | {lang} | {ms:.0f} | {type(e).__name__} | | | | |")
            print(name, lang, e)
            continue
        v = verdict or {}
        cost = usage.get("cost_in_usd_ticks", "")
        out.append(f"| {name} | {lang} | {ms:.0f} | {v.get('verdict', '?')} | {v.get('pattern', '')} | "
                   f"{', '.join(v.get('actions') or [])} | {len(sources)} | {cost} |")
        details += [f"### {name} ({lang}), {ms:.0f} ms", "", f"- **say**: {v.get('say', '')}",
                    f"- **reported recently**: {v.get('reported_recently', '')}",
                    f"- **tool usage**: `{json.dumps(usage.get('server_side_tool_usage_details') or {})}`"]
        details += [f"- source: [{s['title'] or s['url']}]({s['url']})" for s in sources[:8]] + [""]
        print(f"{name:14} {lang} {ms:6.0f} ms  {v.get('verdict')}  sources={len(sources)}  cost={cost}")
    (HERE / "eval" / "RADAR_PROBE.md").write_text("\n".join(out + [""] + details), encoding="utf-8")


if __name__ == "__main__":
    main()
