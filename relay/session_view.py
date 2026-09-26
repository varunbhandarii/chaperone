"""The read-only session page behind the receipt's QR code: GET /sessions/{id}?format=html, and its
dispute-ready record, GET /sessions/{id}/record.json.

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
    "order_status": "Order",
    "order_cancelled": "Cancelled",
    "refund_requested": "Return asked",
    "refund_result": "Refund",
    "mandate_paused": "Agent paused",
    "mandate_resumed": "Agent resumed",
}
STEP_NAMES = {"awaiting_payment": "ordered", "paid": "paid", "preparing": "preparing",
              "ready_for_pickup": "ready for pickup", "picked_up": "picked up", "cancelled": "cancelled",
              "partially_refunded": "partly refunded", "refunded": "refunded"}
# Order fields that stay off the public page and record: the pickup code works at the counter, and the
# payment link is payable.
PRIVATE_ORDER_FIELDS = {"pickup_code", "checkout_url", "card_auth"}


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
    if kind == "order_status":
        return STEP_NAMES.get(str(e.get("status")), str(e.get("status", "")))
    if kind == "order_cancelled":
        return f"{_money(e.get('amount'))} · payment link {e.get('link_status', '')}"
    if kind == "refund_requested":
        return f"{e.get('qty', 1)} × {e.get('sku', '')} · {_money(e.get('amount'))}"
    if kind == "refund_result":
        return f"{_money(e.get('amount'))} {e.get('status', '')} · card ending {e.get('card_last4', '')} · sandbox processor stub"
    return ""


def orders(events: list[dict]) -> list[dict]:
    """Every order in the session, oldest first, with whether it was paid or cancelled."""
    found: dict[str, dict] = {}
    for e in events:
        order_id = e.get("order_id")
        if not order_id or e.get("type") not in ("payment_link_created", "paid", "order_cancelled"):
            continue
        order = found.setdefault(str(order_id), {"order_id": str(order_id), "amount": None, "paid": False, "via": None,
                                                 "cancelled": False})
        if e["type"] == "payment_link_created":
            order["amount"] = e.get("amount")
        elif e["type"] == "order_cancelled":
            order["cancelled"] = True
        else:
            order["paid"], order["via"] = True, e.get("via")
            order["amount"] = order["amount"] or e.get("total", e.get("amount"))
    return list(found.values())


def _receipt(receipt: dict) -> str:
    items = "".join(
        f'<tr><td>{escape(str(i.get("qty", 1)))} × {escape(str(i.get("name", "")))}</td>'
        f'<td>{_money(i.get("price"))}</td></tr>' for i in receipt.get("items") or []
    )
    return (
        f'<section><h2>Receipt</h2><table>{items}<tr class="total"><td>Total</td>'
        f'<td>{_money(receipt.get("total"))}</td></tr></table>'
        f'<p>{escape(str(receipt.get("merchant", "")))} · pickup {escape(str(receipt.get("pickup", "")))}</p>'
        f'<p class="mono small">order {escape(str(receipt.get("order_id", "")))} · decision '
        f'{escape(str(receipt.get("decision_id", "")))}</p></section>')


def _timeline(order: dict) -> str:
    steps = "".join(f'<li>{escape(STEP_NAMES.get(t.get("status"), str(t.get("status", ""))))}'
                    f' <span class="t">{escape(_clock(t.get("at")))}</span></li>' for t in order.get("timeline") or [])
    refunds = "".join(
        f'<li class="refund">Refund {_money(r.get("amount"))} · {escape(str(r.get("qty", 1)))} × {escape(str(r.get("name", "")))}'
        f' · {escape(str(r.get("status", "")))} · card ending {escape(str(r.get("card_last4") or ""))}'
        f' <span class="d">sandbox processor stub</span></li>' for r in order.get("refunds") or [])
    return (f'<section><h2>Order {escape(str(order.get("order_id", "")))} · {_money(order.get("amount"))}</h2>'
            f'<ol class="steps">{steps}</ol>{"<ul>" + refunds + "</ul>" if refunds else ""}</section>')


def _clock(at) -> str:
    try:
        return datetime.datetime.fromisoformat(str(at)).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return ""


def dispute_record(session_id: str, events: list[dict], orders_: list[dict], mandate: dict | None) -> dict:
    """Everything an issuer needs to settle a dispute: the shopper's words, the signed mandate, each policy
    decision, the RFC 9421 signature and the merchant's checks, the payment and any refund."""
    def pick(e: dict, *keys) -> dict:
        return {"at_ms": e.get("rt") or e.get("t"), **{k: e.get(k) for k in keys if e.get(k) is not None}}

    return {
        "kind": "chaperone.dispute_record.v1",
        "session_id": session_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "merchant": "Corner Market (Visa sandbox, no real money)",
        "shopper_words": [pick(e, "text", "lang") for e in transcript(events)
                          if e.get("role") not in ("agent", "assistant")],
        # the credential id is shortened: it identifies the caregiver's passkey, and the record is downloadable
        "mandate": mandate and {**mandate, "credential_id": (str(mandate.get("credential_id") or "")[:16] or None)},
        "decisions": [pick(e, "decision_id", "decision", "rules_failed", "total", "say_key")
                      for e in events if e.get("type") == "policy_decision"],
        "refusals": [pick(e, "rule_id", "rule_ids", "spoken_key", "lang")
                     for e in events if e.get("type") == "refusal"],
        "approvals": [pick(e, "type", "approval_id", "amount", "rule", "approved", "method")
                      for e in events if e.get("type") in ("approval_requested", "approval_result")],
        "signatures": [pick(e, "type", "action", "keyid", "nonce", "created", "expires", "decision_id", "order_id", "checks")
                       for e in events if e.get("type") in ("request_signed", "signature_verified", "signature_rejected")],
        "orders": [{k: v for k, v in o.items() if k not in PRIVATE_ORDER_FIELDS} | {
                       "payment_link": {"backend": (o.get("payment_link") or {}).get("backend")}}
                   for o in orders_],
        "payments": [pick(e, "order_id", "total", "via") for e in events if e.get("type") == "paid"],
        "refunds": [pick(e, "order_id", "refund_id", "status", "amount", "sku", "qty", "reconciliation_id",
                         "card_last4", "label") for e in events if e.get("type") == "refund_result"],
    }


def _record_section(record: dict, session_id: str) -> str:
    mandate = record.get("mandate") or {}
    signed = [s for s in record["signatures"] if s.get("type") == "signature_verified"]
    rows = [
        ("Ruth's words", f'{len(record["shopper_words"])} turns, verbatim'),
        ("Mandate", f'{escape(str(mandate.get("mandate_id", "")))} · '
                    + ("signed by passkey " + escape(str(mandate.get("credential_id") or ""))[:16]
                       if mandate.get("signed") else "unsigned (demo flag)")
                    + f'<br><span class="mono small">sha-256 {escape(str(mandate.get("hash_b64url") or ""))}</span>'),
        ("Decisions", ", ".join(f'{escape(str(d.get("decision_id")))} {escape(str(d.get("decision", "")).upper())}'
                                for d in record["decisions"]) or "none"),
        ("Signatures", ", ".join(f'{escape(str(s.get("keyid", "")))} nonce {escape(str(s.get("nonce", ""))[:12])}… '
                                 f'{sum(1 for c in s.get("checks") or [] if c.get("passed"))}/{len(s.get("checks") or [])}'
                                 for s in signed) or "none"),
        ("Payments", ", ".join(f'{escape(str(p.get("order_id")))} {_money(p.get("total"))}' for p in record["payments"]) or "none"),
        ("Refunds", ", ".join(f'{_money(r.get("amount"))} {escape(str(r.get("status", "")))}' for r in record["refunds"]) or "none"),
    ]
    body = "".join(f'<dt>{k}</dt><dd>{v}</dd>' for k, v in rows)  # every value is escaped where it is built
    return (f'<section><h2>Dispute-ready record</h2><p class="d">What an issuer needs to settle a dispute quickly, '
            f'from the ledger.</p><dl>{body}</dl>'
            f'<p><a href="{escape(session_id)}/record.json" download>Download the record (JSON)</a></p></section>')


def render(session_id: str, events: list[dict], receipts: list[dict] | dict | None = None,
           orders_: list[dict] | None = None, record: dict | None = None) -> str:
    if isinstance(receipts, dict):
        receipts = [receipts]
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

    status = "".join(
        f'<p class="status {"ok" if o["paid"] else "muted" if o.get("cancelled") else "warn"}">'
        f'{"Paid" if o["paid"] else "Cancelled, nothing charged" if o.get("cancelled") else "Waiting for payment"}'
        f' {_money(o["amount"])}'
        f' <span class="mono small">{escape(o["order_id"])}</span></p>'
        for o in orders(events)
    )
    if any(e.get("type") == "refusal" for e in events):
        status = '<p class="status bad">A request was refused and Priyank was told</p>' + status
    status = status or '<p class="status muted">No order in this session</p>'

    receipt_html = "".join(_receipt(r) for r in receipts or [] if r)
    orders_html = "".join(_timeline(o) for o in reversed(orders_ or []))  # oldest first
    record_html = _record_section(record, session_id) if record else ""

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
ol.steps{{display:flex;flex-wrap:wrap;gap:6px;list-style:none;margin:0;padding:0}}ol.steps li{{background:var(--bg);border-radius:999px;padding:2px 12px;font-size:18px}}
.refund{{color:var(--ok)}}dl{{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:8px 0}}dt{{color:var(--muted)}}dd{{margin:0;overflow-wrap:anywhere}}
a{{color:inherit}}
</style></head><body><main>
<header><h1>Ruth's shopping session</h1><p class="mono small">{escape(session_id)} · Corner Market · Visa sandbox, no real money</p>{status}</header>
<section><h2>What was said</h2><ul class="turns">{turns}</ul></section>
<section><h2>What was checked</h2><ul>{steps}</ul></section>
{orders_html}{checks}{receipt_html}{record_html}
</main></body></html>"""
