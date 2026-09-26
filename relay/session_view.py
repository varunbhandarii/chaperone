"""The read-only session page behind the receipt's QR code: GET /sessions/{id}?format=html.

The caregiver app serves it publicly at https://<tunnel-host>/s/<id>, so the page is self-contained: no
scripts, no links back into the relay, and every value is escaped. It reads only that session's ledger
file plus the merchant's receipt for the paid order.
"""

from __future__ import annotations

import datetime
from html import escape

TITLES = {
    "refusal": "Refused",
    "caregiver_alerted": "Priyank alerted",
    "policy_decision": "Policy decision",
    "judge_scored": "Scam check",
    "approval_requested": "Asked Priyank",
    "approval_result": "Priyank answered",
    "request_signed": "Order signed",
    "signature_verified": "Merchant verified the signature",
    "signature_rejected": "Merchant rejected the request",
    "payment_link_created": "Payment link",
    "paid": "Paid",
    "receipt_printed": "Receipt",
}


def _money(value) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def _time(ms) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def transcript(events: list[dict]) -> list[dict]:
    """One line per spoken turn: a later heard event with the same item_id replaces the earlier one."""
    turns: dict[str, dict] = {}
    for i, e in enumerate(events):
        if e.get("type") == "heard" and (e.get("text") or e.get("transcript")):
            turns[str(e.get("item_id") or f"#{i}")] = e
    return sorted(turns.values(), key=lambda e: e.get("seq", 0))


def _step_detail(e: dict) -> str:
    kind = e.get("type")
    if kind == "refusal":
        rules = e.get("rule_ids") or [e.get("rule_id") or e.get("rule") or ""]
        return " + ".join(r for r in rules if r)
    if kind == "policy_decision":
        failed = e.get("rules_failed")
        if failed is None:
            failed = [r.get("id") for r in e.get("rules") or [] if isinstance(r, dict) and r.get("passed") is False]
        verdict = str(e.get("decision", "")).upper()
        return f"{verdict} · {'failed ' + ', '.join(failed) if failed else 'every rule passed'}"
    if kind == "judge_scored":
        return f"score {e.get('scam_score', '')} · {', '.join(e.get('patterns') or []) or 'no scam pattern'}"
    if kind == "approval_requested":
        return f"{_money(e.get('amount'))} · {e.get('rule', '')}"
    if kind == "approval_result":
        return f"{'approved' if e.get('approved') else 'not approved'} · {e.get('method', '')}"
    if kind == "request_signed":
        return f"key {e.get('keyid', '')} · nonce {e.get('nonce', '')}"
    if kind in ("signature_verified", "signature_rejected"):
        checks = e.get("checks") or []
        return f"{sum(1 for c in checks if c.get('passed'))} of {len(checks)} checks passed"
    if kind == "payment_link_created":
        return f"{_money(e.get('amount'))} · {'Visa sandbox' if e.get('backend') == 'visa' else 'mock'}"
    if kind == "paid":
        return f"{_money(e.get('total', e.get('amount')))} · {e.get('via', '')}"
    if kind == "receipt_printed":
        return "printed" if e.get("via") != "screen" else "shown on screen"
    return ""


def render(session_id: str, events: list[dict], receipt: dict | None) -> str:
    turns = "".join(
        f'<li class="{"agent" if e.get("role") in ("agent", "assistant") else "shopper"}">'
        f'<span class="who">{"Agent" if e.get("role") in ("agent", "assistant") else "Ruth"}</span>'
        f'<span class="said">{escape(str(e.get("text") or e.get("transcript") or ""))}</span></li>'
        for e in transcript(events)
    ) or '<li class="muted">No words recorded.</li>'

    steps = "".join(
        f'<li class="{escape(str(e["type"]))}"><span class="t">{_time(e.get("rt") or e.get("t"))}</span>'
        f'<b>{TITLES[e["type"]]}</b> <span class="d">{escape(_step_detail(e))}</span></li>'
        for e in events if e.get("type") in TITLES
    ) or '<li class="muted">Nothing checked yet.</li>'

    # The check that let the order through; later rejections (a replayed order) are still listed in the steps.
    verified = next((e for e in reversed(events) if e.get("type") == "signature_verified"), None) \
        or next((e for e in reversed(events) if e.get("type") == "signature_rejected"), None)
    signed = next((e for e in reversed(events) if e.get("type") == "request_signed"), None)
    checks = ""
    if verified:
        rows = "".join(
            f'<li><span class="{"ok" if c.get("passed") else "bad" if c.get("passed") is False else "muted"}">'
            f'{"✓" if c.get("passed") else "✗" if c.get("passed") is False else "–"}</span> '
            f'<b>{escape(str(c.get("id", "")))}</b> <span class="d">{escape(str(c.get("detail", "")))}</span></li>'
            for c in verified.get("checks") or []
        )
        nonce = verified.get("nonce") or (signed or {}).get("nonce") or ""
        keyid = verified.get("keyid") or (signed or {}).get("keyid") or ""
        checks = (f'<section><h2>Signature</h2><p class="mono">key {escape(str(keyid))}<br>nonce {escape(str(nonce))}'
                  f'<br>decision {escape(str(verified.get("decision_id", "")))}</p><ul class="checks">{rows}</ul></section>')

    paid = next((e for e in reversed(events) if e.get("type") == "paid"), None)
    link = next((e for e in reversed(events) if e.get("type") == "payment_link_created"), None)
    refused = any(e.get("type") == "refusal" for e in events)
    if paid:
        status = f'<p class="status ok">Paid {_money(paid.get("total", paid.get("amount")))}</p>'
    elif link:
        status = f'<p class="status warn">Waiting for payment · {_money(link.get("amount"))}</p>'
    elif refused:
        status = '<p class="status bad">A request was refused and Priyank was told</p>'
    else:
        status = '<p class="status muted">No order in this session</p>'

    receipt_html = ""
    if receipt:
        items = "".join(
            f'<tr><td>{escape(str(i.get("qty", 1)))} × {escape(str(i.get("name", "")))}</td>'
            f'<td>{_money(i.get("price"))}</td></tr>' for i in receipt.get("items") or []
        )
        receipt_html = (
            f'<section><h2>Receipt</h2><table>{items}<tr class="total"><td>Total</td>'
            f'<td>{_money(receipt.get("total"))}</td></tr></table>'
            f'<p>{escape(str(receipt.get("merchant", "")))} · pickup {escape(str(receipt.get("pickup", "")))}</p>'
            f'<p class="mono small">order {escape(str(receipt.get("order_id", "")))} · decision '
            f'{escape(str(receipt.get("decision_id", "")))}</p></section>')

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<meta name="robots" content="noindex">
<title>Chaperone session</title><style>
:root{{--bg:#f6f7fb;--card:#fff;--text:#111;--muted:#667;--ok:#0a6b2b;--bad:#a3160c;--warn:#8a5a00;--line:#e4e6ee}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0b0f17;--card:#121a27;--text:#e8edf5;--muted:#8fa0b8;--ok:#3fb950;--bad:#ff6b61;--warn:#e3b341;--line:#223047}}}}
*{{box-sizing:border-box}}body{{margin:0;padding:16px;background:var(--bg);color:var(--text);font:22px/1.45 system-ui,-apple-system,"Segoe UI","Nirmala UI",sans-serif}}
main{{max-width:760px;margin:auto;display:grid;gap:14px}}
section,header{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 18px}}
h1{{font-size:30px;margin:0}}h2{{font-size:24px;margin:0 0 8px}}
ul{{list-style:none;margin:0;padding:0;display:grid;gap:8px}}
.status{{font-size:30px;font-weight:700;margin:8px 0 0}}
.ok{{color:var(--ok)}}.bad{{color:var(--bad)}}.warn{{color:var(--warn)}}.muted{{color:var(--muted)}}
.who{{display:block;font-size:16px;color:var(--muted);font-weight:600}}.agent .said{{color:var(--muted)}}
.t{{font:16px ui-monospace,Menlo,Consolas,monospace;color:var(--muted);margin-right:8px}}
.d{{color:var(--muted)}}.mono{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:17px;overflow-wrap:anywhere}}
.small{{font-size:15px}}.checks li{{font-size:19px}}
.refusal b,.signature_rejected b{{color:var(--bad)}}.paid b{{color:var(--ok)}}
table{{width:100%;border-collapse:collapse}}td{{padding:6px 0;border-bottom:1px solid var(--line)}}td:last-child{{text-align:right}}
.total td{{font-weight:700;border:0}}
</style></head><body><main>
<header><h1>Ruth's shopping session</h1><p class="mono small">{escape(session_id)} · Corner Market · Visa sandbox, no real money</p>{status}</header>
<section><h2>What was said</h2><ul class="turns">{turns}</ul></section>
<section><h2>What was checked</h2><ul>{steps}</ul></section>
{checks}{receipt_html}
</main></body></html>"""
