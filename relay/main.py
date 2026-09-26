"""Relay: mints ephemeral Grok Voice client secrets for the station browser.

Run from the repo root:
    .venv/Scripts/python -m uvicorn relay.main:app --host 0.0.0.0 --port 8000

Endpoints
    POST /session/token[?seconds=300] -> {"value", "expires_at"}   (503 when XAI_API_KEY is missing)
    GET  /health                      -> {"ok", "service", "xai_key_configured"}

CORS allows every origin because the station page runs on another port and host on the LAN.
The minted secret is returned to the caller only; it is never logged.
"""
from __future__ import annotations

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
