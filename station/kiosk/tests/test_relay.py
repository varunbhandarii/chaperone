"""Relay tests: health, CORS, and ephemeral token minting against a mocked xAI endpoint.

Run from the repo root:  .venv/Scripts/python -m pytest station/kiosk/tests -q
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from relay import main, tokens

SECRET = "xai-realtime-client-secret-TESTVALUE"
KEY = "xai-test-key-DO-NOT-LOG"


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(tokens, "load_dotenv", lambda *a, **k: False)  # ignore a real .env on this machine


@pytest.fixture
def xai(monkeypatch):
    """Mock api.x.ai; returns the list of captured requests. Set `reply` to change the response."""
    monkeypatch.setenv("XAI_API_KEY", KEY)
    seen: list[httpx.Request] = []
    state = {"reply": lambda req: httpx.Response(200, json={"value": SECRET, "expires_at": 1_900_000_000})}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return state["reply"](request)

    monkeypatch.setattr(tokens, "TRANSPORT", httpx.MockTransport(handler))
    return seen, state


def test_health(client, no_key):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["service"] == "relay"
    assert body["xai_key_configured"] is False


def test_token_without_key_is_503_with_hint(client, no_key):
    r = client.post("/session/token")
    assert r.status_code == 503
    assert "XAI_API_KEY" in r.json()["error"]
    assert r.headers["cache-control"] == "no-store"


def test_token_minted(client, xai, capsys):
    seen, _ = xai
    r = client.post("/session/token")
    assert r.status_code == 200
    assert r.json() == {"value": SECRET, "expires_at": 1_900_000_000}
    assert r.headers["cache-control"] == "no-store"

    (req,) = seen
    assert str(req.url) == "https://api.x.ai/v1/realtime/client_secrets"
    assert req.method == "POST"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(req.content) == {"expires_after": {"seconds": 300}}

    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert KEY not in out.out + out.err
    assert "minted" in out.out


def test_token_seconds_param(client, xai):
    seen, _ = xai
    assert client.post("/session/token?seconds=60").status_code == 200
    assert json.loads(seen[-1].content) == {"expires_after": {"seconds": 60}}
    assert client.post("/session/token?seconds=5").status_code == 422
    assert client.post("/session/token?seconds=7200").status_code == 422


def test_upstream_rejection_is_502_without_secrets(client, xai, capsys):
    _, state = xai
    state["reply"] = lambda req: httpx.Response(401, json={"error": "Incorrect API key provided"})
    r = client.post("/session/token")
    assert r.status_code == 502
    assert "401" in r.json()["error"] and "Incorrect API key" in r.json()["error"]
    out = capsys.readouterr()
    assert KEY not in r.text + out.out + out.err


def test_upstream_unreachable_is_502(client, xai):
    _, state = xai

    def boom(req):
        raise httpx.ConnectError("no route", request=req)

    state["reply"] = boom
    r = client.post("/session/token")
    assert r.status_code == 502
    assert "could not reach api.x.ai" in r.json()["error"]


def test_upstream_bad_body_is_502(client, xai):
    _, state = xai
    state["reply"] = lambda req: httpx.Response(200, json={"unexpected": True})
    r = client.post("/session/token")
    assert r.status_code == 502
    assert "missing value" in r.json()["error"]


def test_cors_allows_only_the_station_origins(client):
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        r = client.options("/session/token", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == origin
    # any other page on the LAN cannot mint voice tokens from a browser
    r = client.options("/session/token", headers={"Origin": "http://192.168.8.13:3000", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_relay_mounts_ledger_routes_only_when_present(client):
    paths = {route.path for route in main.app.routes}
    assert {"/session/token", "/health"} <= paths
    ledger_routes = any(p.startswith(("/events", "/sessions", "/audio", "/.well-known")) for p in paths)
    assert ledger_routes == (main.ledger is not None)
