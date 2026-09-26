"""Chaperone Line: the station's tools for a phone call, as a remote MCP server.

Grok's Voice Agent Builder answers the phone number and calls these tools over MCP (Streamable HTTP) at
https://<TUNNEL_HOST>/line/mcp; the caregiver app rewrites /line/* to this service. Every route except /health
needs `Authorization: Bearer $LINE_MCP_TOKEN`. The same handlers answer plain JSON at POST /api/<tool> for the
Builder's api_request tool (the call is keyed by the X-Call-Id header or a call_id field).

A phone call has no station screen, so safety lives in the tools: scam_check, the read-back gate (checkout only
right after read_cart), the refund preview gate, and policy's checkout (mandate, rule screen and judge) run on
Ruth's words, which every tool takes as `ruth_said`.

    python -m uvicorn line.server:app --host 127.0.0.1 --port 8005
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from common import tls
from common.config import ROOT, env


class _MaskToken(logging.Filter):
    """The token rides in the path for URL-only MCP clients (/k/<token>/mcp): keep it out of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(re.sub(r"/k/[^/\s]+", "/k/***", a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_MaskToken())

VOICE = json.loads((ROOT / "station" / "config" / "voice.json").read_text(encoding="utf-8"))
MANDATE_ID = VOICE["mandate_id"]
MERCHANT_ID = VOICE["merchant"]
BILLERS: dict[str, dict] = VOICE.get("billers", {})


def service(name: str, port: int) -> str:
    return (env(f"{name.upper()}_URL") or f"http://127.0.0.1:{port}").rstrip("/")


POLICY, MERCHANT, CATALOG, RELAY = service("policy", 8001), service("merchant", 8002), service("catalog", 8003), service("relay", 8000)
MAX_QTY = 24
CALL_IDLE_S = 2 * 3600

# ---------------------------------------------------------------- spoken lines (the shared line files, then these)

FALLBACK_LINES: dict[str, dict[str, str]] = {
    "store_unavailable": {
        "en": "I can't reach the store just now. Please try again in a minute.",
        "es": "No puedo comunicarme con la tienda ahora mismo. Intente otra vez en un minuto.",
        "hi": "अभी दुकान से संपर्क नहीं हो पा रहा। थोड़ी देर बाद फिर से कोशिश कीजिए।",
    },
    "scam_check_unavailable": {
        "en": "I can't check that right now. Please don't pay anyone or share any codes until you talk to Priyank.",
        "es": "No puedo revisarlo ahora mismo. Por favor no le pague a nadie ni dé ningún código hasta hablar con Priyank.",
        "hi": "मैं अभी इसकी जाँच नहीं कर पा रही। प्रियंक से बात करने तक किसी को पैसे न दें और कोई कोड न बताएँ।",
    },
    "read_back_required": {
        "en": "Let me read your order back first.",
        "es": "Primero le leo su pedido.",
        "hi": "पहले मैं आपका ऑर्डर पढ़कर सुनाती हूँ।",
    },
    "no_orders": {
        "en": "I don't see an order from today yet.",
        "es": "Todavía no veo un pedido de hoy.",
        "hi": "आज का कोई ऑर्डर अभी नहीं दिख रहा।",
    },
    "order_status": {"en": "Your order is {status}.", "es": "Su pedido está {status}.", "hi": "आपका ऑर्डर {status}।"},
    "order_status_pickup": {
        "en": "Your order is {status}. It will be ready for pickup after 3 pm, and your pickup code is {code}.",
        "es": "Su pedido está {status}. Estará listo para recoger después de las 3, y su código de recogida es {code}.",
        "hi": "आपका ऑर्डर {status}। यह दोपहर 3 बजे के बाद ले जाने के लिए तैयार होगा, और आपका पिकअप कोड {code} है।",
    },
    "bill_due": {
        "en": "Your {biller} bill is {amount}, due {due}. It is not past due.",
        "es": "Su factura de {biller} es de {amount} y vence el {due}. No está atrasada.",
        "hi": "आपका {biller} का बिल {amount} है, जो {due} तक भरना है। यह बकाया नहीं है।",
    },
    "bill_past_due": {
        "en": "Your {biller} bill is {amount}, and it was due {due}.",
        "es": "Su factura de {biller} es de {amount} y venció el {due}.",
        "hi": "आपका {biller} का बिल {amount} है, जो {due} को भरना था।",
    },
    "bill_paid": {
        "en": "Your {biller} bill is paid. You owe nothing right now.",
        "es": "Su factura de {biller} está pagada. No debe nada ahora.",
        "hi": "आपका {biller} का बिल भरा हुआ है। अभी कुछ बकाया नहीं है।",
    },
    "cart_empty": {"en": "Your cart is empty.", "es": "Su carrito está vacío.", "hi": "आपकी कार्ट खाली है।"},
    "order_cancelled": {"en": "I cancelled your order. Nothing was charged.", "es": "Cancelé su pedido. No se le cobró nada.", "hi": "आपका ऑर्डर रद्द कर दिया है। कोई पैसा नहीं कटा।"},
    "cancel_too_late": {
        "en": "That order is already paid, so it can't be cancelled. I can return items for you instead.",
        "es": "Ese pedido ya está pagado, así que no se puede cancelar. Puedo devolver los artículos, si quiere.",
        "hi": "उस ऑर्डर का भुगतान हो चुका है, इसलिए वह रद्द नहीं हो सकता। मैं सामान वापस करवा सकती हूँ।",
    },
    "history_summary": {"en": "You placed {count} orders in the last {days} days, {spent} in all.", "es": "Hizo {count} pedidos en los últimos {days} días, {spent} en total.", "hi": "पिछले {days} दिनों में आपने {count} ऑर्डर किए, कुल {spent}।"},
    "history_summary_one": {"en": "You placed one order in the last {days} days, for {spent}.", "es": "Hizo un pedido en los últimos {days} días, de {spent}.", "hi": "पिछले {days} दिनों में आपने एक ऑर्डर किया, {spent} का।"},
    "history_last": {"en": "The last one had {items}.", "es": "El último tenía {items}.", "hi": "पिछले ऑर्डर में {items} था।"},
    "history_none": {"en": "I don't see any orders in the last {days} days.", "es": "No veo pedidos en los últimos {days} días.", "hi": "पिछले {days} दिनों में कोई ऑर्डर नहीं दिखा।"},
}
STATUS_WORDS = {
    "awaiting_payment": {"en": "waiting for payment", "es": "esperando el pago", "hi": "भुगतान का इंतज़ार कर रहा है"},
    "paid": {"en": "paid", "es": "pagado", "hi": "भुगतान हो गया है"},
    "preparing": {"en": "being prepared", "es": "en preparación", "hi": "तैयार हो रहा है"},
    "ready_for_pickup": {"en": "ready for pickup", "es": "listo para recoger", "hi": "ले जाने के लिए तैयार है"},
    "picked_up": {"en": "picked up", "es": "recogido", "hi": "ले जाया जा चुका है"},
    "cancelled": {"en": "cancelled", "es": "cancelado", "hi": "रद्द हो गया है"},
    "partially_refunded": {"en": "partly refunded", "es": "reembolsado en parte", "hi": "कुछ पैसे वापस हो गए हैं"},
    "refunded": {"en": "refunded", "es": "reembolsado", "hi": "पैसे वापस हो गए हैं"},
}
MONTHS = {
    "en": "January February March April May June July August September October November December".split(),
    "es": "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(),
    "hi": "जनवरी फ़रवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर".split(),
}


def _load_lines() -> dict[str, dict[str, str]]:
    lines: dict[str, dict[str, str]] = {k: dict(v) for k, v in FALLBACK_LINES.items()}
    for lang in ("en", "es", "hi"):
        path = ROOT / "ai" / "prompts" / f"lines.{lang}.json"
        if path.exists():
            for key, text in json.loads(path.read_text(encoding="utf-8")).items():
                if isinstance(text, str):
                    lines.setdefault(key, {})[lang] = text
    return lines


LINES = _load_lines()


def say(key: str, lang: str, **slots: str) -> str:
    table = LINES.get(key) or LINES.get("declined") or {}
    text = table.get(lang) or table.get("en") or ""
    text = re.sub(r"\{(\w+)\}", lambda m: slots.get(m.group(1), ""), text)
    return text.replace("$X", slots.get("total", ""))


def money(cents: int, lang: str) -> str:
    dollars, rest = divmod(int(cents), 100)
    if lang == "es":
        if not dollars and rest:
            return f"{rest} centavos"
        d = f"{dollars} {'dólar' if dollars == 1 else 'dólares'}"
        return f"{d} con {rest} centavos" if rest else d
    if lang == "hi":
        if not dollars and rest:
            return f"{rest} सेंट"
        return f"{dollars} डॉलर {rest} सेंट" if rest else f"{dollars} डॉलर"
    return f"${cents / 100:.2f}"


def cents(value: Any) -> int:
    return int(round(float(value) * 100))


def spoken_date(iso: str | None, lang: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return iso or ""
    month, day = MONTHS[lang][int(m.group(2)) - 1], int(m.group(3))
    return f"{month} {day}" if lang == "en" else f"{day} de {month}" if lang == "es" else f"{day} {month}"


def spoken_code(code: Any) -> str:
    return "-".join(re.sub(r"\D", "", str(code or "")))


def guess_lang(text: str) -> str | None:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    low = f" {text.lower()} "
    if re.search(r"[ñ¿¡áéíóú]", low) or any(w in low for w in (" el ", " la ", " mi ", " que ", " necesito ", " quiero ", " por favor ", " sí ")):
        return "es"
    if re.search(r"\b(mujhe|chahiye|kya|mera|meri|hai|nahi)\b", low):
        return "hi"
    return "en" if re.search(r"[a-z]", low) else None


# ---------------------------------------------------------------- one phone call

@dataclass
class Call:
    session_id: str
    # handed to the agent on every result; the Builder opens a new MCP session per tool call, so the agent passes it back
    call_id: str = field(default_factory=lambda: "c_" + secrets.token_hex(4))
    lang: str = "en"
    items: dict[str, dict] = field(default_factory=dict)  # every sku offered on this call (search or bill)
    cart: dict[str, int] = field(default_factory=dict)  # sku -> qty, in the order added
    version: int = 0
    last_cart_tool: str | None = None
    read_version: int | None = None
    heard: list[str] = field(default_factory=list)
    orders: list[str] = field(default_factory=list)
    refund_preview: dict | None = None
    last_refund_tool: str | None = None
    seen: float = field(default_factory=time.time)
    last_args: dict = field(default_factory=dict)


CALLS: dict[str, Call] = {}  # key (call_id, MCP session id, X-Call-Id) -> the phone call it belongs to
REUSE_S = 120  # a new MCP session this soon after the last tool call, with no call_id, continues that call
# (kept short: two callers back to back must not share a cart or an order to cancel)


def call_for(key: str | None) -> Call:
    now = time.time()
    for stale in [k for k, c in CALLS.items() if now - c.seen > CALL_IDLE_S]:
        CALLS.pop(stale, None)
    key = key or "line-default"
    call = CALLS.get(key)
    if call is None:
        call = CALLS[key] = Call(session_id="s_line_" + secrets.token_hex(5))
        _post_event(call, "session_started", channel="line")
    call.seen = now
    return call


def heard(call: Call, ruth_said: str | None) -> None:
    text = (ruth_said or "").strip()
    if not text or (call.heard and call.heard[-1] == text):
        return
    call.heard.append(text[:1000])
    call.lang = guess_lang(text) or call.lang
    _post_event(call, "heard", role="shopper", text=text[:1000], lang=call.lang)


# ---------------------------------------------------------------- HTTP to the other services

_client: httpx.AsyncClient | None = None
_schema_warned = False


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(verify=tls.context(), timeout=httpx.Timeout(15.0, connect=2.0))
    return _client


def _post_event(call: Call, type_: str, **fields: Any) -> None:
    """Best effort, off the call's path: the ledger must never slow Ruth down."""
    import asyncio

    event = {"type": type_, "session_id": call.session_id, "mandate_id": MANDATE_ID, "t": int(time.time() * 1000), "source": "line", **fields}

    async def send() -> None:
        global _schema_warned
        try:
            res = await client().post(f"{RELAY}/events", json=event, timeout=2.0)
            if res.status_code >= 400 and not _schema_warned:
                _schema_warned = True
                print(f"line: the relay refused a {type_} event ({res.status_code}); is source 'line' in contracts/events.schema.json yet?")
        except httpx.HTTPError:
            pass

    try:
        asyncio.get_running_loop().create_task(send())
    except RuntimeError:
        pass


async def get_json(url: str, **kw: Any) -> tuple[int, Any]:
    try:
        res = await client().get(url, **kw)
        return res.status_code, (res.json() if res.content else None)
    except (httpx.HTTPError, ValueError):
        return 0, None


async def post_json(url: str, body: dict, **kw: Any) -> tuple[int, Any]:
    try:
        res = await client().post(url, json=body, **kw)
        return res.status_code, (res.json() if res.content else None)
    except (httpx.HTTPError, ValueError):
        return 0, None


# ---------------------------------------------------------------- the tools (shared by MCP and /api)

def compact(item: dict) -> dict:
    out = {"sku": item["sku"], "name": item.get("name"), "price": item.get("price")}
    for key in ("brand", "size", "store", "usual"):
        if item.get(key) not in (None, ""):
            out[key] = item[key]
    if item.get("profile_label"):
        out["shopper_calls_it"] = item["profile_label"]
    others = [{"store": e.get("store"), "price": e.get("price")} for e in item.get("elsewhere") or [] if isinstance(e, dict) and e.get("store")]
    if others:
        out["elsewhere"] = others[:3]
    return out


def cart_view(call: Call) -> dict:
    lines = []
    total = 0
    for sku, qty in call.cart.items():
        item = call.items[sku]
        line_total = cents(item["price"]) * qty
        total += line_total
        lines.append({"sku": sku, "name": item.get("name"), "qty": qty, "price": item.get("price"), "line_total": line_total / 100,
                      **({"store": item["store"]} if item.get("store") else {})})
    return {"lines": lines, "total": total / 100, "total_cents": total}


def read_back(call: Call, view: dict) -> str:
    lang = call.lang
    parts = []
    for line in view["lines"]:
        qty = f"{line['qty']} " if line["qty"] > 1 else ""
        store = f" ({line['store']})" if line.get("store") else ""
        parts.append(f"{qty}{line['name']}{store}, {money(cents(line['line_total']), lang)}")
    items, total = "; ".join(parts), money(view["total_cents"], lang)
    if lang == "es":
        return f"Su pedido: {items}. Total: {total}. ¿Hago el pedido?"
    if lang == "hi":
        return f"आपका ऑर्डर: {items}। कुल {total}। क्या मैं ऑर्डर कर दूँ?"
    return f"Your order: {items}. Total {total}. Shall I place the order?"


async def t_budget_left(call: Call) -> dict:
    status, body = await get_json(f"{POLICY}/budget", params={"mandate_id": MANDATE_ID}, timeout=3.0)
    if status != 200 or not isinstance(body, dict):
        return {"error": "budget unavailable", "say": say("store_unavailable", call.lang)}
    return {**body, "say": say("budget_left", call.lang, left=money(cents(body.get("left", 0)), call.lang))}


async def t_search(call: Call, query: str, store: str | None) -> dict:
    query = (query or "").strip()
    if not query:
        return {"error": "query is required"}
    params = {"q": query, "limit": 3, **({"store": store} if store else {})}
    (s1, resolved), (s2, found) = await _gather(get_json(f"{CATALOG}/resolve", params={"q": query}, timeout=3.0),
                                               get_json(f"{CATALOG}/search", params=params, timeout=3.0))
    if s2 != 200 or not isinstance(found, dict):
        return {"error": "catalog unavailable", "say": say("store_unavailable", call.lang)}
    items: list[dict] = []
    for match in (resolved or {}).get("matches") or [] if s1 == 200 else []:
        item = match.get("item") if isinstance(match, dict) else None
        if isinstance(item, dict) and item.get("sku"):
            items.append({**item, "usual": True, **({"profile_label": match["label"]} if match.get("label") else {})})
    for item in found.get("items") or []:
        if isinstance(item, dict) and item.get("sku") and all(i["sku"] != item["sku"] for i in items):
            items.append(item)
    items = items[:3]
    for item in items:
        call.items[item["sku"]] = item
    return {"query": query, "items": [compact(i) for i in items]}


async def _gather(*aws):
    import asyncio

    return await asyncio.gather(*aws)


def t_add(call: Call, sku: str, qty: int) -> dict:
    sku = (sku or "").strip()
    if sku not in call.items:
        # the agent may pass a name or a sku from another call: show what this call has seen, so it can pick one
        match = [k for k, i in call.items.items() if sku and sku.lower() in f"{k} {i.get('name', '')}".lower()]
        if len(match) == 1:
            sku = match[0]
        else:
            known = [{"sku": k, "name": i.get("name"), "price": i.get("price")} for k, i in list(call.items.items())[-6:]]
            return {"error": f"unknown sku {sku or '(none)'}", "known_items": known,
                    "instruction": "Use a sku from known_items, or call search_catalog first and pass the same call_id."}
    qty = int(qty or 1)
    if qty < 1 or call.cart.get(sku, 0) + qty > MAX_QTY:
        return {"error": f"qty must be from 1 to {MAX_QTY}"}
    call.cart[sku] = call.cart.get(sku, 0) + qty
    call.version += 1
    call.last_cart_tool = "add"
    return {"ok": True, "added": {"sku": sku, "qty": qty, "name": call.items[sku].get("name")}, "cart": cart_view(call)}


def t_remove(call: Call, sku: str, qty: int | None) -> dict:
    if sku not in call.cart:
        return {"error": f"{sku} is not in the cart", "cart": cart_view(call)}
    left = 0 if not qty else max(0, call.cart[sku] - int(qty))
    if left:
        call.cart[sku] = left
    else:
        call.cart.pop(sku)
    call.version += 1
    call.last_cart_tool = "remove"
    return {"ok": True, "removed": sku, "qty_left": left, "cart": cart_view(call)}


def t_read_cart(call: Call) -> dict:
    view = cart_view(call)
    call.last_cart_tool = "read"
    if not view["lines"]:
        return {**view, "say": say("cart_empty", call.lang),
                "instruction": "The cart is empty. Do not call read_cart again: call add_to_cart with a sku from search_catalog (same call_id), then read_cart."}
    call.read_version = call.version
    return {"lines": view["lines"], "total": view["total"], "say": read_back(call, view)}


async def t_checkout(call: Call) -> dict:
    view = cart_view(call)
    # The read-back gate on the phone: checkout only right after read_cart, for exactly that cart.
    if not view["lines"] or call.last_cart_tool != "read" or call.read_version != call.version:
        return {"error": "read_back_required", "say": say("read_back_required", call.lang),
                "instruction": "Call read_cart, say its say text, wait for Ruth's yes, then call checkout."}
    call.last_cart_tool = "checkout"
    merchants = {call.items[sku].get("merchant") for sku in call.cart} - {None}
    body = {
        "session_id": call.session_id, "mandate_id": MANDATE_ID, "read_back": True, "lang": call.lang, "channel": "line",
        "cart": {"merchant": next(iter(merchants)) if len(merchants) == 1 else MERCHANT_ID,
                 "items": [{"sku": l["sku"], "name": l["name"], "qty": l["qty"], "price": l["price"],
                            "category": call.items[l["sku"]].get("category", "")} for l in view["lines"]],
                 "total": view["total"]},
        **({"transcript": " ".join(call.heard)[-2000:]} if call.heard else {}),
    }
    status, reply = await post_json(f"{POLICY}/checkout", body, timeout=30.0)
    if status != 200 or not isinstance(reply, dict):
        detail = (reply or {}).get("detail") if isinstance(reply, dict) else None
        return {"status": "error", "error": detail or f"policy answered {status or 'nothing'}", "say": say("checkout_unavailable", call.lang)}
    decision = reply.get("decision")
    total = money(cents(view["total"]), call.lang)
    order = reply.get("order") or {}
    if decision == "allow":
        placed = [o["order_id"] for o in reply.get("orders") or [] if isinstance(o, dict) and o.get("order_id")]
        placed = placed or ([order["order_id"]] if order.get("order_id") else [])
        if not placed:  # allowed, but no store took the order: nothing was bought
            return {"status": "error", "error": reply.get("order_error") or "the merchant did not take the order",
                    "say": say("checkout_unavailable", call.lang)}
        call.orders.extend(placed)
        call.cart.clear()
        call.version += 1
        return {"status": "ordered", "order_id": order.get("order_id"), "decision_id": reply.get("decision_id"), "say": say("ordering_now", call.lang, total=total)}
    if decision == "approve":
        return {"status": "waiting_for_caregiver", "decision_id": reply.get("decision_id"), "say": say("asking_priya", call.lang, total=total)}
    key = reply.get("say_key") if reply.get("say_key") in LINES else "declined"
    return {"status": "declined", "decision_id": reply.get("decision_id"), "say_key": reply.get("say_key"), "say": say(key, call.lang, total=total)}


async def t_scam_check(call: Call, story: str, caller_org: str | None, caller_phone: str | None) -> dict:
    story = (story or "").strip() or " ".join(call.heard[-3:])
    if not story:
        return {"error": "story is required: pass Ruth's own words"}
    call.lang = guess_lang(story) or call.lang
    body = {"session_id": call.session_id, "mandate_id": MANDATE_ID, "lang": call.lang, "channel": "line", "story": story,
            "caller": {"org": caller_org or None, "name": None, "phone": caller_phone or None}}
    words = " ".join(call.heard[-3:])
    if words and words != story:
        body["transcript"] = words
    status, reply = await post_json(f"{POLICY}/scam-check", body, timeout=14.0)
    if status != 200 or not isinstance(reply, dict) or reply.get("verdict") not in ("scam", "unsure", "ok") or not reply.get("say"):
        return {"error": "not ready", "say": say("scam_check_unavailable", call.lang)}
    return {
        "verdict": reply["verdict"], "pattern": reply.get("pattern"), "say": reply["say"], "actions": reply.get("actions") or [],
        "sources": [s.get("title") for s in reply.get("sources") or [] if isinstance(s, dict) and s.get("title")],
        "instruction": "Say the say text exactly, calmly, and offer its one action. Do not add a warning of your own.",
    }


async def t_bill_status(call: Call, biller: str | None) -> dict:
    said = (biller or "").lower()
    bid = next((b for b in BILLERS if b == said.replace(" ", "_") or said in (BILLERS[b]["name"].lower(), "")), None)
    if bid is None and re.search(r"peachtree|power|electric|luz|बिजली|bijli", said):
        bid = next((b for b in BILLERS if "power" in b), None)
    if bid is None:
        return {"error": "unknown biller", "billers": [b["name"] for b in BILLERS.values()]}
    ref = BILLERS[bid]["account_ref"]
    status, mandate = await get_json(f"{POLICY}/mandate", timeout=2.5)
    for b in ((mandate or {}).get("mandate") or {}).get("billers") or [] if status == 200 else []:
        if isinstance(b, dict) and b.get("merchant_id") == bid and b.get("account_ref"):
            ref = b["account_ref"]
    status, bill = await get_json(f"{MERCHANT}/billers/{bid}/accounts/{ref}", timeout=3.0)
    if status != 200 or not isinstance(bill, dict) or bill.get("balance_due") is None:
        return {"error": "not ready", "say": say("store_unavailable", call.lang)}
    name, balance = BILLERS[bid]["name"], float(bill["balance_due"])
    key = "bill_paid" if balance <= 0 else "bill_past_due" if bill.get("past_due") else "bill_due"
    out = {"biller": name, "balance_due": balance, "due_date": bill.get("due_date"), "past_due": bool(bill.get("past_due")),
           "say": say(key, call.lang, biller=name, amount=money(cents(balance), call.lang), due=spoken_date(bill.get("due_date"), call.lang))}
    if balance > 0:
        sku = f"BILL-{bid}"
        call.items[sku] = {"sku": sku, "name": f"{name} bill …{ref[-4:]}", "price": balance, "category": "utility_bill", "merchant": bid, "store": name}
        out["sku"] = sku
    return out


async def t_order_status(call: Call, order_id: str | None) -> dict:
    oid = order_id or (call.orders[-1] if call.orders else None)
    if not oid:
        return {"error": "no_orders", "say": say("no_orders", call.lang)}
    status, order = await get_json(f"{MERCHANT}/orders/{oid}", timeout=3.0)
    if status != 200 or not isinstance(order, dict):
        return {"error": "order status unavailable", "say": say("store_unavailable", call.lang)}
    state = str(order.get("status") or "")
    progress = order.get("fulfilment") if state == "partially_refunded" else state
    words = STATUS_WORDS.get(state, {}).get(call.lang, state.replace("_", " "))
    code = spoken_code(order.get("pickup_code"))
    if progress == "ready_for_pickup" and code:
        line = say("order_ready", call.lang, code=code, pickup_code=code)
    elif progress in ("paid", "preparing") and code:
        line = say("order_status_pickup", call.lang, status=words, code=code)
    else:
        line = say("order_status", call.lang, status=words)
    return {"order_id": oid, "status": state, "pickup_code": order.get("pickup_code"), "say": line}


async def t_refund(call: Call, order_id: str | None, sku: str | None, qty: int | None, reason: str, confirmed: bool) -> dict:
    oid = order_id or (call.orders[-1] if call.orders else None)
    if not oid:
        return {"error": "no_orders", "say": say("no_orders", call.lang)}
    target = {"order_id": oid, "sku": sku or "", "qty": int(qty or 0)}
    if confirmed and (call.last_refund_tool != "preview" or call.refund_preview != target):
        # The phone has no turn signal: a refund goes through only right after its own preview.
        return {"error": "refund_confirm_required", "instruction": "Call request_refund with confirmed false, say its say text, wait for Ruth's yes."}
    body = {"session_id": call.session_id, "mandate_id": MANDATE_ID, "order_id": oid, **({"sku": sku} if sku else {}),
            **({"qty": int(qty)} if qty else {}), "reason": (reason or "")[:200], "confirmed": bool(confirmed), "lang": call.lang,
            "transcript": " ".join(call.heard[-2:])}
    status, reply = await post_json(f"{POLICY}/refunds", body, timeout=15.0)
    call.last_refund_tool = "refund"
    call.refund_preview = None
    if not isinstance(reply, dict):
        return {"status": "error", "say": say("store_unavailable", call.lang)}
    preview = reply.get("preview")
    if not confirmed and isinstance(preview, dict) and preview.get("amount") is not None:
        call.refund_preview, call.last_refund_tool = target, "preview"
        last4 = spoken_code(preview.get("card_last4"))
        return {"status": "preview", "amount": preview["amount"], "say": say("refund_preview", call.lang, amount=money(cents(preview["amount"]), call.lang), last4=last4, card_last4=last4)}
    refund = reply.get("refund") if isinstance(reply.get("refund"), dict) else reply
    if confirmed and str(refund.get("status", "")).upper() in ("PENDING", "TRANSMITTED", "SETTLED", "COMPLETED"):
        amount = (refund.get("refundAmountDetails") or {}).get("refundAmount")
        return {"status": refund["status"], "say": say("refund_done", call.lang, amount=money(cents(amount or 0), call.lang), last4="")}
    key = reply.get("say_key") if reply.get("say_key") in LINES else "declined"
    return {"status": "declined", "say_key": reply.get("say_key"), "say": say(key, call.lang)}


async def t_cancel(call: Call, order_id: str | None) -> dict:
    oid = order_id or (call.orders[-1] if call.orders else None)
    if not oid:
        return {"error": "no_orders", "say": say("no_orders", call.lang)}
    status, reply = await post_json(f"{POLICY}/orders/{oid}/cancel", {"session_id": call.session_id, "mandate_id": MANDATE_ID, "lang": call.lang}, timeout=15.0)
    reply = reply if isinstance(reply, dict) else {}
    if 200 <= status < 300 and (reply.get("status") == "cancelled" or reply.get("cancelled") is True):
        return {"status": "cancelled", "order_id": oid, "link_status": reply.get("link_status"), "say": say("order_cancelled", call.lang)}
    if status == 409 or reply.get("say_key") == "cancel_too_late":
        return {"status": "not_cancelled", "reason": "already paid", "order_id": oid, "say": say("cancel_too_late", call.lang)}
    return {"status": "error", "say": say("store_unavailable", call.lang)}


def _item_words(line: Any) -> str:
    if isinstance(line, str):
        m = re.match(r"\s*(\d+)\s*x\s+(.+)$", line, re.I)
        return (f"{m.group(1)} {m.group(2)}" if m and int(m.group(1)) > 1 else m.group(2) if m else line).strip()
    if isinstance(line, dict):
        qty = int(line.get("qty") or 1)
        return f"{qty} {line.get('name', '')}".strip() if qty > 1 else str(line.get("name", ""))
    return ""


async def t_history(call: Call, days: int | None) -> dict:
    days = max(1, min(60, int(days or 30)))
    status, body = await get_json(f"{POLICY}/history", params={"mandate_id": MANDATE_ID, "days": days}, timeout=4.0)
    if status != 200 or not isinstance(body, dict):
        return {"error": "history unavailable", "say": say("store_unavailable", call.lang)}
    kept = [o for o in body.get("orders") or [] if isinstance(o, dict) and o.get("status") != "cancelled"]
    kept.sort(key=lambda o: str(o.get("at") or ""), reverse=True)
    spent = sum(cents(o.get("total") or 0) for o in kept)
    items = [w for w in (_item_words(i) for i in (kept[0].get("items") or [])[:3]) if w] if kept else []
    slots = {"days": str(days), "count": str(len(kept)), "spent": money(spent, call.lang), "items": ", ".join(items)}
    if not kept:
        line = say("history_none", call.lang, **slots)
    else:
        line = say("history_summary_one" if len(kept) == 1 else "history_summary", call.lang, **slots)
        if items:
            line += " " + say("history_last", call.lang, **slots)
    return {"days": days, "orders": [{"order_id": o.get("order_id"), "status": o.get("status"), "total": o.get("total")} for o in kept[:5]], "say": line}


# ---------------------------------------------------------------- MCP

mcp = MCPServer(
    "chaperone-tools",
    instructions=(
        "Ruth's Chaperone tools. Every result has a call_id: pass it as call_id on every later tool call in this phone call. "
        "Pass Ruth's latest words as ruth_said. Say each result's say text."
    ),
)


# What each tool does, for clients that hold back tools that may change things: an undeclared tool counts as destructive.
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


def _call(ctx: Context, ruth_said: str = "", call_id: str = "") -> Call:
    """The phone call a tool call belongs to: by the call_id the agent passed back, else by this MCP session, else the
    call that ran a tool in the last few minutes (the Builder opens a new session for every tool call), else a new one."""
    headers = ctx.headers or {}
    session = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
    now = time.time()
    call = CALLS.get(call_id.strip()) if call_id and call_id.strip() else None
    if call is None and session:
        call = CALLS.get(session)
    if call is None:
        recent = max(CALLS.values(), key=lambda c: c.seen, default=None)
        if recent is not None and now - recent.seen < REUSE_S:
            call = recent
    if call is None:
        call = call_for(None if not session else session)
    CALLS[call.call_id] = call
    if session:
        CALLS[session] = call
    call.seen = now
    heard(call, ruth_said)
    call.last_args = {k: v for k, v in (ctx.request_context.request.params.arguments or {}).items() if k not in ("ruth_said", "call_id")} if _has_args(ctx) else {}
    return call


def _has_args(ctx: Context) -> bool:
    try:
        return isinstance(ctx.request_context.request.params.arguments, dict)
    except AttributeError:
        return False


def _done(call: Call, out: dict, tool: str = "") -> dict:
    out = {**out, "call_id": call.call_id}
    # One line per tool call on stdout (run.sh line tees it to .claude/logs/line.log): enough to follow a call.
    brief = {k: out[k] for k in ("error", "ok", "status", "verdict") if k in out}
    if call.last_args:
        brief = {"args": call.last_args, **brief}
    if "items" in out:
        brief["items"] = [i.get("sku") for i in out["items"]]
    if "lines" in out:
        brief["cart"] = [f"{l['qty']}x{l['sku']}" for l in out["lines"]]
    print(f"{time.strftime('%H:%M:%S')} line {tool or '?'} {call.call_id} {json.dumps(brief, ensure_ascii=False)[:200]} say={str(out.get('say', ''))[:70]!r}", flush=True)
    return out


@mcp.tool(annotations=READ)
async def scam_check(story: str, ctx: Context, caller_org: str = "", caller_phone: str = "", ruth_said: str = "", call_id: str = "") -> dict:
    """Check whether a call, text, email, pop-up or visitor asking Ruth for money is a scam. Call it first whenever anyone asks her for money, gift cards, a wire, crypto, a refund, card numbers, codes or remote access. story: what happened, in her own words. Returns verdict, say (say it exactly) and actions."""
    call = _call(ctx, ruth_said or story, call_id)
    return _done(call, await t_scam_check(call, story, caller_org, caller_phone), "scam_check")


@mcp.tool(annotations=READ)
async def budget_left(ctx: Context, ruth_said: str = "", call_id: str = "") -> dict:
    """How much Ruth can still spend this month under the rules she and Priyank signed. Returns say."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, await t_budget_left(call), "budget_left")


@mcp.tool(annotations=READ)
async def search_catalog(query: str, ctx: Context, store: str = "", ruth_said: str = "", call_id: str = "") -> dict:
    """Search Ruth's approved stores. query: a short English product query ("bread") or her words for a personal item ("my blood pressure medicine"). Returns up to three items with sku, name, store, price and usual."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, await t_search(call, query, store or None), "search_catalog")


@mcp.tool(annotations=WRITE)
async def add_to_cart(sku: str, ctx: Context, qty: int = 1, ruth_said: str = "", call_id: str = "") -> dict:
    """Add an item to Ruth's cart: a sku from search_catalog, or the sku bill_status gave for a bill. Pass the call_id from earlier results."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, t_add(call, sku, qty), "add_to_cart")


@mcp.tool(annotations=WRITE)
async def remove_from_cart(sku: str, ctx: Context, qty: int = 0, ruth_said: str = "", call_id: str = "") -> dict:
    """Remove an item from the cart; qty 0 removes the whole line."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, t_remove(call, sku, qty or None), "remove_from_cart")


@mcp.tool(annotations=READ)
async def read_cart(ctx: Context, ruth_said: str = "", call_id: str = "") -> dict:
    """The cart and say: the exact read-back sentence. Say it word for word and wait for Ruth's yes before checkout."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, t_read_cart(call), "read_cart")


@mcp.tool(annotations=WRITE)
async def checkout(ctx: Context, ruth_said: str = "", call_id: str = "") -> dict:
    """Place the order for the cart that was just read back. Only right after read_cart and Ruth's yes; pass her yes as ruth_said. Returns say."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, await t_checkout(call), "checkout")


@mcp.tool(annotations=READ)
async def bill_status(ctx: Context, biller: str = "", ruth_said: str = "", call_id: str = "") -> dict:
    """What Ruth really owes an approved biller (Peachtree Power): balance, due date, past due or not. Returns say, and a sku to pay it through the cart."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, await t_bill_status(call, biller or None), "bill_status")


@mcp.tool(annotations=READ)
async def order_status(ctx: Context, order_id: str = "", ruth_said: str = "", call_id: str = "") -> dict:
    """Where Ruth's order is, with the pickup code. Returns say."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, await t_order_status(call, order_id or None), "order_status")


@mcp.tool(annotations=WRITE)
async def request_refund(reason: str, confirmed: bool, ctx: Context, order_id: str = "", sku: str = "", qty: int = 0, ruth_said: str = "", call_id: str = "") -> dict:
    """Return items from a paid order; money only goes back to the card that paid. Call with confirmed false, say the say text, wait for her yes, then call again with confirmed true and the same order_id, sku and qty."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, await t_refund(call, order_id or None, sku or None, qty or None, reason, confirmed), "request_refund")


@mcp.tool(annotations=DESTRUCTIVE)
async def cancel_order(ctx: Context, order_id: str = "", ruth_said: str = "", call_id: str = "") -> dict:
    """Cancel an order that has not been paid yet. A paid order cannot be cancelled; offer a return instead. Returns say."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, await t_cancel(call, order_id or None), "cancel_order")


@mcp.tool(annotations=READ)
async def purchase_history(ctx: Context, days: int = 30, ruth_said: str = "", call_id: str = "") -> dict:
    """What Ruth bought recently: how many orders, what they came to, and the latest one's items. Returns say."""
    call = _call(ctx, ruth_said, call_id)
    return _done(call, await t_history(call, days), "purchase_history")


# ---------------------------------------------------------------- plain JSON for the Builder's api_request

API = {
    "scam_check": lambda c, a: t_scam_check(c, a.get("story", ""), a.get("caller_org"), a.get("caller_phone")),
    "budget_left": lambda c, a: t_budget_left(c),
    "search_catalog": lambda c, a: t_search(c, a.get("query", ""), a.get("store")),
    "add_to_cart": lambda c, a: t_add(c, a.get("sku", ""), a.get("qty", 1)),
    "remove_from_cart": lambda c, a: t_remove(c, a.get("sku", ""), a.get("qty")),
    "read_cart": lambda c, a: t_read_cart(c),
    "checkout": lambda c, a: t_checkout(c),
    "bill_status": lambda c, a: t_bill_status(c, a.get("biller")),
    "order_status": lambda c, a: t_order_status(c, a.get("order_id")),
    "cancel_order": lambda c, a: t_cancel(c, a.get("order_id")),
    "purchase_history": lambda c, a: t_history(c, a.get("days")),
    "request_refund": lambda c, a: t_refund(c, a.get("order_id"), a.get("sku"), a.get("qty"), a.get("reason", ""), a.get("confirmed") is True),
}


@mcp.custom_route("/api/{tool}", methods=["POST"])
async def api(request: Request) -> JSONResponse:
    handler = API.get(request.path_params["tool"])
    if handler is None:
        return JSONResponse({"error": "unknown tool", "tools": sorted(API)}, status_code=404)
    try:
        args = await request.json()
    except ValueError:
        args = {}
    args = args if isinstance(args, dict) else {}
    call = call_for(request.headers.get("x-call-id") or args.get("call_id"))
    heard(call, args.get("ruth_said") or (args.get("story") if request.path_params["tool"] == "scam_check" else None))
    result = handler(call, args)
    if hasattr(result, "__await__"):
        result = await result
    return JSONResponse(result)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "line", "calls": len(CALLS), "token_set": bool(os.environ.get("LINE_MCP_TOKEN"))})


# ---------------------------------------------------------------- the app, behind a bearer token

class BearerGate:
    """Every route but /health needs LINE_MCP_TOKEN, as `Authorization: Bearer <token>` or inside the path
    (`/k/<token>/mcp`, for MCP clients such as the Voice Agent Builder whose form takes only a URL and would
    otherwise ask for OAuth). Without a token set, nothing is served."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/health":
            return await self.app(scope, receive, send)
        token = os.environ.get("LINE_MCP_TOKEN", "")
        path = scope.get("path") or ""
        ok = False
        if token and path.startswith("/k/"):
            given, _, rest = path[3:].partition("/")
            if hmac.compare_digest(given.encode(), token.encode()):
                ok = True
                scope = {**scope, "path": "/" + rest, "raw_path": ("/" + rest).encode()}
        elif token:
            given = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
            ok = hmac.compare_digest(given.encode(), f"Bearer {token}".encode())
        if not ok:
            status = 503 if not token else 401
            response = JSONResponse({"error": "LINE_MCP_TOKEN is not set" if not token else "unauthorized"}, status_code=status)
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def _security() -> TransportSecuritySettings:
    """Host checks against DNS rebinding: loopback, plus the tunnel host the caregiver app forwards from."""
    tunnel = (env("TUNNEL_HOST") or "").split("://")[-1].strip("/")
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"] + ([tunnel, f"{tunnel}:*"] if tunnel else [])
    origins = ["http://127.0.0.1:*", "http://localhost:*"] + ([f"https://{tunnel}"] if tunnel else [])
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


# JSON responses (no SSE on POST) pass cleanly through the tunnel and the caregiver app's rewrite.
app = BearerGate(mcp.streamable_http_app(json_response=True, transport_security=_security(), host="127.0.0.1"))
