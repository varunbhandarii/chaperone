"""Relay: mints ephemeral Grok Voice client secrets for the station browser.

Run from the repo root:
    .venv/Scripts/python -m uvicorn relay.main:app --host 0.0.0.0 --port 8000

Endpoints
    POST /session/token[?seconds=300] -> {"value", "expires_at"}   (503 when XAI_API_KEY is missing)
    GET  /health                      -> {"ok", "service", "xai_key_configured"}

CORS allows only the station page's origins (STATION_ORIGINS, comma-separated), so another page open on a
LAN machine cannot mint voice tokens. The wall is same-origin and the caregiver app calls the relay from its
server, so neither needs an entry. The minted secret is returned to the caller only; it is never logged.

The ledger routes (/events, /events/stream, /sessions, JWKS, /audio, /wall, /reset) live in relay/ledger.py
and are mounted here when that module is present.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from relay import tokens

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    state = "configured" if tokens.has_key() else "MISSING (POST /session/token will answer 503)"
    print(f"[relay] XAI_API_KEY {state}", flush=True)
    yield


app = FastAPI(title="Chaperone relay", version="0.1.0", lifespan=lifespan)
STATION_ORIGINS = [
    o.strip() for o in os.getenv("STATION_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=STATION_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Chaperone-Host"],
)

# Ledger routes ship in their own module; the token endpoint keeps working if it is missing or broken.
try:
    from relay import ledger
except ModuleNotFoundError as exc:
    ledger = None
    missing = "relay/ledger.py not present" if exc.name == "relay.ledger" else f"ledger import failed: {exc!r}"
    print(f"[relay] {missing}; ledger routes not mounted", flush=True)
except Exception as exc:  # noqa: BLE001
    ledger = None
    print(f"[relay] ledger routes failed to load, not mounted: {exc!r}", flush=True)
if ledger is not None:
    app.include_router(ledger.router)

NO_STORE = {"Cache-Control": "no-store"}


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "relay", "xai_key_configured": tokens.has_key(), "t": int(time.time() * 1000)}


@app.post("/session/token")
async def session_token(seconds: int = Query(300, ge=tokens.MIN_SECONDS, le=tokens.MAX_SECONDS)) -> JSONResponse:
    started = time.perf_counter()
    try:
        secret = await tokens.mint_client_secret(seconds)
    except tokens.TokenError as exc:
        print(f"[relay] token request failed ({exc.status}): {exc}", flush=True)
        return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=NO_STORE)
    took = (time.perf_counter() - started) * 1000
    print(f"[relay] client secret minted in {took:.0f} ms (expires_at={secret['expires_at']})", flush=True)
    return JSONResponse(secret, headers=NO_STORE)
