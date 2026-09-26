"""The line's MCP server end to end: a real MCP client over Streamable HTTP, the station's mock services behind it."""

import asyncio
import importlib
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import httpx2
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "station" / "kiosk" / "tests"))
import mock_realtime  # noqa: E402

TOKEN = "test-line-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    return server


@pytest.fixture(scope="module")
def line_url():
    mock_port, line_port = free_port(), free_port()
    mock = serve(mock_realtime.app, mock_port)
    mock_base = f"http://127.0.0.1:{mock_port}"
    saved = {k: os.environ.get(k) for k in ("POLICY_URL", "MERCHANT_URL", "CATALOG_URL", "RELAY_URL", "LINE_MCP_TOKEN")}
    for name in ("POLICY_URL", "MERCHANT_URL", "CATALOG_URL", "RELAY_URL"):
        os.environ[name] = mock_base
    os.environ["LINE_MCP_TOKEN"] = TOKEN
    import line.server

    server_module = importlib.reload(line.server)  # service URLs are read at import
    line = serve(server_module.app, line_port)
    httpx.post(f"{mock_base}/mock/reset", json={})
    yield f"http://127.0.0.1:{line_port}", mock_base, server_module
    line.should_exit = True
    mock.should_exit = True
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def api(base: str, tool: str, call_id: str, **args) -> dict:
    res = httpx.post(f"{base}/api/{tool}", json=args, headers={"Authorization": f"Bearer {TOKEN}", "X-Call-Id": call_id}, timeout=20)
    assert res.status_code == 200, res.text
    return res.json()


def test_only_health_is_open(line_url):
    base, _, _ = line_url
    assert httpx.get(f"{base}/health").json()["ok"] is True
    assert httpx.post(f"{base}/api/budget_left", json={}).status_code == 401
    assert httpx.post(f"{base}/api/budget_left", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert httpx.post(f"{base}/mcp", json={}).status_code == 401


def test_mcp_client_shops_with_the_read_back_gate(line_url):
    base, mock_base, server = line_url

    async def flow() -> dict:
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as http:
            async with streamable_http_client(f"{base}/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = sorted(t.name for t in (await session.list_tools()).tools)

                    async def call(name, **args):
                        result = await session.call_tool(name, args)
                        assert not result.is_error, result.content
                        return result.structured_content or json.loads(result.content[0].text)

                    found = await call("search_catalog", query="bread", ruth_said="Necesito pan")
                    usual = next(i for i in found["items"] if i.get("usual"))
                    await call("add_to_cart", sku=usual["sku"])
                    early = await call("checkout", ruth_said="sí")  # no read-back yet
                    back = await call("read_cart")
                    done = await call("checkout", ruth_said="sí")
                    again = await call("checkout", ruth_said="sí")  # the gate re-arms after a checkout
                    return {"names": names, "early": early, "back": back, "done": done, "again": again}

    out = asyncio.run(flow())
    assert out["names"] == sorted(["scam_check", "budget_left", "search_catalog", "add_to_cart", "remove_from_cart", "read_cart",
                                   "checkout", "bill_status", "order_status", "request_refund", "cancel_order", "purchase_history"])
    assert out["early"]["error"] == "read_back_required"
    assert out["back"]["say"].startswith("Su pedido:")  # Ruth spoke Spanish
    assert out["done"]["status"] == "ordered", out["done"]
    assert out["again"]["error"] == "read_back_required"
    checkout = [e for e in httpx.get(f"{mock_base}/mock/log").json() if e.get("path") == "/checkout"][-1]["body"]
    assert checkout["read_back"] is True and checkout["channel"] == "line"
    assert "Necesito pan" in checkout["transcript"]  # policy's screen and judge read Ruth's words
    # the cart lived in this call's MCP session, not in a shared default
    assert "line-default" not in server.CALLS and any(len(k) >= 16 for k in server.CALLS)


def test_scam_check_bill_and_refund_gate_over_api(line_url):
    base, mock_base, _ = line_url
    scam = api(base, "scam_check", "call-2", story="Mi nieto llamó, está en la cárcel y necesita 2000 dólares para la fianza")
    assert scam["verdict"] == "scam" and scam["say"].startswith("Esto es una estafa")
    assert "instruction" in scam and scam["sources"]
    sent = [e for e in httpx.get(f"{mock_base}/mock/log").json() if e.get("path") == "/scam-check"][-1]["body"]
    assert sent["channel"] == "line" and sent["lang"] == "es"

    bill = api(base, "bill_status", "call-2")
    assert bill["sku"] == "BILL-peachtree_power" and bill["balance_due"] == 86.4
    assert "15 de octubre" in bill["say"]

    held = api(base, "request_refund", "call-2", order_id="o_x", sku="BAK-001", reason="return", confirmed=True)
    assert held["error"] == "refund_confirm_required"


def test_scam_check_falls_back_to_a_safe_line_when_policy_has_none_yet(line_url):
    base, mock_base, _ = line_url
    httpx.post(f"{mock_base}/mock/reset", json={"scam_ready": False})
    try:
        out = api(base, "scam_check", "call-3", story="A man from Microsoft wants me to install AnyDesk")
        assert out["error"] == "not ready"
        assert "don't pay anyone" in out["say"]
    finally:
        httpx.post(f"{mock_base}/mock/reset", json={})


def test_lines_and_money(line_url):
    _, _, server = line_url
    assert server.money(50, "es") == "50 centavos"
    assert server.money(349, "hi") == "3 डॉलर 49 सेंट"
    assert server.spoken_date("2026-10-15", "en") == "October 15"
    assert server.guess_lang("मेरा बिल कितना है") == "hi"
    assert server.guess_lang("what do I owe") == "en"
    assert "{" not in server.say("bill_due", "hi", biller="Peachtree Power", amount="86 डॉलर", due="15 अक्टूबर")


def test_cancel_and_history_over_api(line_url):
    base, _, _ = line_url
    assert api(base, "cancel_order", "call-4")["error"] == "no_orders"  # nothing ordered on this call yet
    history = api(base, "purchase_history", "call-4", days=30, ruth_said="what did I buy")
    assert history["say"].startswith(("You placed", "I don't see"))


def test_the_token_can_ride_in_the_path_for_url_only_clients(line_url):
    base, _, _ = line_url
    assert httpx.post(f"{base}/k/wrong-token/api/budget_left", json={}).status_code == 401
    ok = httpx.post(f"{base}/k/{TOKEN}/api/budget_left", json={}, headers={"X-Call-Id": "call-5"})
    assert ok.status_code == 200 and "say" in ok.json()

    async def tools() -> list[str]:
        async with httpx2.AsyncClient(timeout=30) as http:
            async with streamable_http_client(f"{base}/k/{TOKEN}/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return [t.name for t in (await session.list_tools()).tools]

    assert "scam_check" in asyncio.run(tools())


def one_shot(base: str, name: str, args: dict) -> dict:
    """Like the Voice Agent Builder: a new MCP session for every tool call."""

    async def run() -> dict:
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as http:
            async with streamable_http_client(f"{base}/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, args)
                    return result.structured_content or json.loads(result.content[0].text)

    return asyncio.run(run())


def test_one_cart_per_phone_call_across_the_builders_one_shot_sessions(line_url):
    base, _, server = line_url
    server.CALLS.clear()
    found = one_shot(base, "search_catalog", {"query": "bread", "ruth_said": "I need bread"})
    cid = found["call_id"]
    usual = next(i for i in found["items"] if i.get("usual"))
    assert one_shot(base, "add_to_cart", {"sku": usual["sku"], "call_id": cid})["ok"] is True
    back = one_shot(base, "read_cart", {"call_id": cid})
    assert back["lines"] and back["call_id"] == cid
    # the agent forgot the call_id: a session this soon after the last tool call continues the same call
    assert one_shot(base, "read_cart", {})["call_id"] == cid
    # a call that went quiet longer than REUSE_S starts fresh
    for call in {id(c): c for c in server.CALLS.values()}.values():
        call.seen -= server.REUSE_S + 1
    assert one_shot(base, "read_cart", {})["call_id"] != cid
