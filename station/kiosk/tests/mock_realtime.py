"""Local stand-in for the Grok Voice realtime API and the station's HTTP services, for tests without credentials.

It follows the documented event flow closely enough to exercise the station page and ws_probe.mjs:
session.created -> session.update/updated (validated: PCM rate, manual turn detection, both tools)
-> user turn (audio commit or input_text) -> response.create -> a short spoken preamble plus a
search_catalog function call -> function_call_output -> response.create -> a spoken answer.
It also supports response.cancel, input_audio_buffer.clear, conversation.item.truncate and
force_message. Audio is a quiet sine tone at the session's output rate.

HTTP stand-ins: POST /session/token, GET /search, POST /screen, POST /checkout, POST /events.
GET /mock/log returns every client event and HTTP call it saw.

Run standalone (repo root):
    .venv/Scripts/python -m uvicorn mock_realtime:app --app-dir station/kiosk/tests --port 8010
then open http://localhost:5173/?host=127.0.0.1&relay=http://127.0.0.1:8010&catalog=http://127.0.0.1:8010&policy=http://127.0.0.1:8010&ws=ws://127.0.0.1:8010/v1/realtime
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import secrets
import struct
import time

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="mock realtime + services")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

LOG: list[dict] = []
RATES = {8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000}
FIRST_AUDIO_DELAY_S = 0.15
CHUNK_S = 0.04
BLOCKED_WORDS = ("tarjeta", "tarjetas", "regalo", "gift card", "gift cards")
MOCK = {"transcript": "necesito pan", "pace": 0.25, "clip": False}  # pace 1.0 streams audio in real time

BREAD = [
    {"sku": "bread_white_20oz", "name": "White bread", "brand": "Corner", "category": "grocery", "price": 2.29, "size": "20 oz", "usual": False},
    {"sku": "bread_ww_20oz", "name": "Whole wheat bread", "brand": "Corner", "category": "grocery", "price": 3.49, "size": "20 oz", "usual": True},
    {"sku": "bread_sourdough", "name": "Sourdough loaf", "brand": "Bakehouse", "category": "grocery", "price": 4.99, "size": "24 oz", "usual": False},
]


def record(kind: str, **fields) -> None:
    LOG.append({"kind": kind, "t": int(time.time() * 1000), **fields})


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def tone(seconds: float, rate: int) -> bytes:
    n = int(seconds * rate)
    return b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n))


# ---------------------------------------------------------------- HTTP stand-ins

@app.post("/session/token")
async def token() -> dict:
    record("http", path="/session/token")
    return {"value": "mock-secret-" + secrets.token_hex(8), "expires_at": int(time.time()) + 300}


@app.get("/search")
async def search(q: str = "", limit: int = 3) -> dict:
    record("http", path="/search", q=q, limit=limit)
    ql = q.lower()
    results = BREAD if ("bread" in ql or "pan" in ql) else []
    return {"query": q, "results": results[:limit], "took_ms": 1}


@app.post("/screen")
async def screen(request: Request) -> dict:
    body = await request.json()
    record("http", path="/screen", body=body)
    text = str(body.get("text", "")).lower()
    if any(w in text for w in BLOCKED_WORDS):
        return {
            "action": "refuse",
            "hits": [{"rule_id": "R1_blocked_category", "pattern": "gift_card"}],
            "refusal": {
                "rule_id": "R1_blocked_category",
                "spoken_key": "blocked_gift_card",
                "patterns": ["gift_card"],
                "lang": "es-MX",
                "text": "No puedo comprar tarjetas de regalo en esta cuenta. Ya le avisé a Priyank.",
                "audio_url": "/warnings/refusal_es-MX.mp3",
            },
        }
    return {"action": "proceed", "hits": [], "refusal": None}


@app.post("/checkout")
async def checkout(request: Request) -> dict:
    body = await request.json()
    record("http", path="/checkout", body=body)
    return {
        "decision_id": "d_mock_0001",
        "session_id": body.get("session_id"),
        "mandate_id": body.get("mandate_id"),
        "decision": "allow",
        "rules": [{"id": "R4_per_purchase_cap", "passed": True}],
        "monthly_total_after": 100.0,
    }


@app.post("/events")
async def events(request: Request) -> dict:
    record("event", body=await request.json())
    return {"ok": True}


@app.get("/warnings/{name}")
async def warning_clip(name: str) -> Response:
    """A refusal clip (WAV tone) when enabled with /mock/reset {"clip": true}; 404 otherwise."""
    record("http", path=f"/warnings/{name}", served=MOCK["clip"])
    if not MOCK["clip"]:
        return Response(status_code=404)
    pcm = tone(0.6, 24000)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16) + b"data" + struct.pack("<I", len(pcm))
    return Response(header + pcm, media_type="audio/wav")


@app.get("/mock/log")
async def mock_log() -> list[dict]:
    return LOG


@app.post("/mock/reset")
async def mock_reset(request: Request) -> dict:
    LOG.clear()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        if body.get("transcript"):
            MOCK["transcript"] = str(body["transcript"])
        if body.get("pace") is not None:
            MOCK["pace"] = float(body["pace"])
        if body.get("clip") is not None:
            MOCK["clip"] = bool(body["clip"])
    return {"ok": True}


# ---------------------------------------------------------------- realtime

class Session:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.lock = asyncio.Lock()
        self.config: dict = {}
        self.rate = 24000
        self.buffer_bytes = 0
        self.task: asyncio.Task | None = None
        self.response_id: str | None = None
        self.tool_outputs: list[dict] = []
        self.last_user = ""
        self.items: list[str] = []

    async def send(self, event: dict) -> None:
        event.setdefault("event_id", new_id("event"))
        async with self.lock:
            await self.ws.send_text(json.dumps(event))

    async def error(self, message: str, param: str | None = None, etype: str = "invalid_request_error") -> None:
        await self.send({"type": "error", "error": {"type": etype, "code": etype, "message": message, "param": param}})

    # -- session.update validation mirrors the documented schema
    async def session_update(self, session: dict) -> None:
        audio = session.get("audio", {})
        rin = audio.get("input", {}).get("format", {}).get("rate")
        rout = audio.get("output", {}).get("format", {}).get("rate")
        if rin not in RATES or rout not in RATES:
            return await self.error(f"unsupported PCM rate {rin}/{rout}", "session.audio.format.rate")
        td = session.get("turn_detection", "missing")
        if not (td is None or (isinstance(td, dict) and td.get("type") is None)):
            return await self.error("mock expects manual turn detection", "session.turn_detection")
        names = [t.get("name") for t in session.get("tools", []) if t.get("type") == "function"]
        if "search_catalog" not in names:
            return await self.error("search_catalog tool missing", "session.tools")
        self.config = session
        self.rate = int(rout)
        await self.send({
            "type": "session.updated",
            "session": {
                "id": new_id("sess"), "object": "realtime.session", "model": "grok-voice-think-fast-2.0",
                "voice": session.get("voice"), "instructions": session.get("instructions", ""),
                "turn_detection": {"type": None}, "tools": session.get("tools", []),
            },
        })

    async def commit(self) -> None:
        if self.buffer_bytes == 0:
            return await self.error("input audio buffer is empty", "input_audio_buffer")
        item_id = new_id("item")
        prev = self.items[-1] if self.items else None
        self.items.append(item_id)
        seconds = self.buffer_bytes / 2 / self.rate
        self.buffer_bytes = 0
        await self.send({"type": "input_audio_buffer.committed", "item_id": item_id, "previous_item_id": prev})
        self.last_user = MOCK["transcript"]
        record("mock", note=f"committed {seconds:.2f}s of audio")

        async def transcribe():
            await asyncio.sleep(0.2)
            await self.send({"type": "conversation.item.input_audio_transcription.updated", "item_id": item_id, "content_index": 0, "transcript": self.last_user.split(" ")[0]})
            await asyncio.sleep(0.15)
            await self.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": item_id, "content_index": 0, "transcript": self.last_user})

        asyncio.create_task(transcribe())

    async def speak(self, text: str, seconds: float, tool_call: dict | None = None) -> None:
        rid = new_id("resp")
        self.response_id = rid
        item_id = new_id("item")
        try:
            await self.send({"type": "response.created", "response": {"id": rid, "object": "realtime.response", "status": "in_progress", "output": []}})
            await asyncio.sleep(FIRST_AUDIO_DELAY_S)
            await self.send({"type": "response.output_item.added", "response_id": rid, "output_index": 0, "item": {"id": item_id, "object": "realtime.item", "type": "message", "role": "assistant", "status": "in_progress", "content": []}})
            pcm = tone(seconds, self.rate)
            step = int(CHUNK_S * self.rate) * 2
            words = text.split(" ")
            for i, off in enumerate(range(0, len(pcm), step)):
                await self.send({"type": "response.output_audio.delta", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0, "delta": base64.b64encode(pcm[off:off + step]).decode()})
                if i < len(words):
                    await self.send({"type": "response.output_audio_transcript.delta", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0, "delta": (" " if i else "") + words[i]})
                await asyncio.sleep(CHUNK_S * MOCK["pace"])
            rest = " ".join(words[len(range(0, len(pcm), step)):])
            if rest:
                await self.send({"type": "response.output_audio_transcript.delta", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0, "delta": " " + rest})
            await self.send({"type": "response.output_audio.done", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0})
            await self.send({"type": "response.output_audio_transcript.done", "response_id": rid, "item_id": item_id, "output_index": 0, "content_index": 0, "transcript": text})
            if tool_call:
                fc_id, call_id = new_id("item"), new_id("call")
                await self.send({"type": "response.output_item.added", "response_id": rid, "output_index": 1, "item": {"id": fc_id, "object": "realtime.item", "type": "function_call", "status": "in_progress", "call_id": call_id, "name": tool_call["name"]}})
                await self.send({"type": "response.function_call_arguments.done", "response_id": rid, "item_id": fc_id, "output_index": 1, "call_id": call_id, "name": tool_call["name"], "arguments": json.dumps(tool_call["arguments"])})
            self.response_id = None  # a response.create right after response.done must be accepted
            await self.send({"type": "response.done", "response": {"id": rid, "object": "realtime.response", "status": "completed", "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}}})
        except asyncio.CancelledError:
            await self.send({"type": "response.done", "response": {"id": rid, "object": "realtime.response", "status": "cancelled"}})
            raise
        finally:
            if self.response_id == rid:
                self.response_id = None

    def start(self, coro) -> None:
        self.response_id = "starting"
        self.task = asyncio.create_task(coro)

    async def response_create(self, response: dict | None) -> None:
        if self.response_id is not None:
            return await self.error("a response is already in progress", "response")
        instructions = (response or {}).get("instructions")
        if instructions:
            text = instructions.split("nothing else:", 1)[-1].strip()
            return self.start(self.speak(text, 0.8))
        if self.tool_outputs:
            outputs, self.tool_outputs = self.tool_outputs, []
            data = json.loads(outputs[-1].get("output") or "{}")
            if data.get("refused"):
                return self.start(self.speak(data.get("say", ""), 1.0))
            if "results" in data:
                names = ", ".join(f"{r['name']} {r['price']}" for r in data["results"])
                return self.start(self.speak(f"Tengo {names}. ¿Cuál quiere?", 1.2))
            if "decision" in data:
                return self.start(self.speak(f"Listo, decisión {data['decision']}.", 0.8))
            return self.start(self.speak("Lo siento, hubo un problema.", 0.8))
        words = set(self.last_user.lower().replace(",", " ").replace(".", " ").split())
        if words & {"sí", "si", "yes"}:
            return self.start(self.speak("Un momento.", 0.4, {"name": "checkout", "arguments": {"items": [{"sku": "bread_ww_20oz", "qty": 1}]}}))
        if "pan" in self.last_user.lower() or "bread" in self.last_user.lower():
            return self.start(self.speak("Un momento.", 0.4, {"name": "search_catalog", "arguments": {"query": "bread"}}))
        return self.start(self.speak("¿En qué le puedo ayudar?", 0.8))

    async def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()


@app.websocket("/v1/realtime")
async def realtime(ws: WebSocket) -> None:
    protocols = ws.scope.get("subprotocols") or []
    secret = next((p for p in protocols if p.startswith("xai-client-secret.")), None)
    record("ws_connect", model=ws.query_params.get("model"), subprotocol_ok=bool(secret))
    if not secret:
        await ws.close(code=4401)
        return
    await ws.accept(subprotocol=secret)
    s = Session(ws)
    await s.send({"type": "session.created", "session": {"id": new_id("sess"), "object": "realtime.session", "model": ws.query_params.get("model"), "voice": "ara", "turn_detection": {"type": "server_vad"}}})
    await s.send({"type": "conversation.created", "conversation": {"id": new_id("conv"), "object": "realtime.conversation"}})
    try:
        while True:
            ev = json.loads(await ws.receive_text())
            et = ev.get("type")
            if et == "input_audio_buffer.append":
                n = len(base64.b64decode(ev.get("audio", "")))
                s.buffer_bytes += n
                record("client", type=et, bytes=n)
                continue
            record("client", type=et, event=ev)
            if et == "session.update":
                await s.session_update(ev.get("session", {}))
            elif et == "input_audio_buffer.commit":
                await s.commit()
            elif et == "input_audio_buffer.clear":
                s.buffer_bytes = 0
                await s.send({"type": "input_audio_buffer.cleared"})
            elif et == "conversation.item.create":
                item = ev.get("item", {})
                if item.get("type") == "function_call_output":
                    s.tool_outputs.append(item)
                elif item.get("type") == "force_message":
                    s.start(s.speak(item["content"][0]["text"], 1.0))
                elif item.get("type") == "message" and item.get("role") == "user":
                    s.last_user = " ".join(c.get("text", "") for c in item.get("content", []))
                await s.send({"type": "conversation.item.added", "item": {"id": new_id("item"), "type": item.get("type"), "role": item.get("role")}})
            elif et == "response.create":
                await s.response_create(ev.get("response"))
            elif et == "response.cancel":
                await s.cancel()
            elif et == "conversation.item.truncate":
                await s.send({"type": "conversation.item.truncated", "item_id": ev.get("item_id"), "content_index": 0, "audio_end_ms": ev.get("audio_end_ms", 0)})
            else:
                await s.error(f"unsupported event {et}", etype="invalid_event")
    except WebSocketDisconnect:
        record("ws_disconnect")
        await s.cancel()
