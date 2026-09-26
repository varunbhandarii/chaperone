"""ws_probe.mjs against the local realtime mock: token via the relay path, session.update shape,
text turn, search_catalog round trip, transcripts and first-audio timing. Needs Node 22+ on PATH.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

import mock_realtime

HERE = Path(__file__).resolve().parent
KIOSK = HERE.parent
ROOT = KIOSK.parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_url():
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(mock_realtime.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/mock/log", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def run_probe(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, str(KIOSK / "ws_probe.mjs"), *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=40, env=env,
    )


def test_probe_round_trip(mock_url):
    httpx.post(f"{mock_url}/mock/reset")
    ws = mock_url.replace("http://", "ws://") + "/v1/realtime"
    proc = run_probe("--relay", mock_url, "--ws-url", ws, "--timeout", "15")
    out = proc.stdout
    print(out)
    assert proc.returncode == 0, proc.stderr
    assert "RESULT: PASS" in out
    assert "TOOL CALL search_catalog" in out
    assert "AGENT: Un momento." not in out  # tools are called without a spoken preamble
    assert "AGENT: Tengo" in out
    assert "first audio delta after response.create" in out  # the spoken reply after the tool result
    assert "mock-secret-" not in out  # the token is never printed

    log = httpx.get(f"{mock_url}/mock/log").json()
    connect = next(e for e in log if e["kind"] == "ws_connect")
    assert connect["subprotocol_ok"] and connect["model"] == "grok-voice-think-fast-2.0"

    client = [e for e in log if e["kind"] == "client"]
    types = [e["type"] for e in client]
    assert types[:3] == ["session.update", "conversation.item.create", "response.create"]
    session = client[0]["event"]["session"]
    voice = json.loads((ROOT / "station/config/voice.json").read_text(encoding="utf-8"))
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["turn_detection"] == {"type": None}
    assert session["reasoning"] == {"effort": "none"}
    assert session["instructions"] == voice["session"]["instructions"]
    assert [t["name"] for t in session["tools"]] == ["scam_check", "search_catalog", "bill_status", "add_to_cart", "remove_from_cart", "read_cart",
                                                    "budget_left", "checkout", "order_status", "cancel_order", "request_refund", "purchase_history"]
    assert "one moment" not in voice["session"]["instructions"].lower().replace("never say \"one moment\"", "")
    checkout_tool = next(t for t in session["tools"] if t["name"] == "checkout")
    assert checkout_tool["parameters"] == {"type": "object", "properties": {}}  # checkout takes no list from the model

    outputs = [e["event"]["item"] for e in client if e["type"] == "conversation.item.create" and e["event"]["item"]["type"] == "function_call_output"]
    assert len(outputs) == 1
    result = json.loads(outputs[0]["output"])
    assert result["source"] == "fallback" and [r["sku"] for r in result["items"]] == ["BAK-001", "BAK-003", "BAK-002"]
    assert types[-1] == "response.create"


def test_probe_uses_catalog_when_asked(mock_url):
    httpx.post(f"{mock_url}/mock/reset")
    ws = mock_url.replace("http://", "ws://") + "/v1/realtime"
    env = {**os.environ, "CATALOG_URL": mock_url}
    proc = run_probe("--relay", mock_url, "--ws-url", ws, "--catalog", "--timeout", "15", env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "search_catalog({\"query\": \"bread\"}) -> catalog" in proc.stdout


def test_probe_without_relay_or_key_explains(tmp_path):
    env_file = ROOT / ".env"
    if env_file.exists() and "XAI_API_KEY=" in env_file.read_text(encoding="utf-8").replace("XAI_API_KEY=\n", ""):
        pytest.skip("a real XAI_API_KEY is configured on this machine")
    env = {k: v for k, v in os.environ.items() if k != "XAI_API_KEY"}
    proc = run_probe("--relay", "http://127.0.0.1:9", "--timeout", "5", env=env)
    assert proc.returncode == 2
    assert "XAI_API_KEY is not set" in proc.stderr
