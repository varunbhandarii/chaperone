"""Relay ledger: the event log everything posts to, and the live stream the wall and caregiver read.

relay/main.py mounts this with `app.include_router(ledger.router)`. Standalone for a laptop test:
    python -m uvicorn relay.ledger:app --port 8010

POST /events                       validate (contracts/events.schema.json), add rt + seq, append to
                                   sessions/by-id/<session_id>.jsonl and sessions/live.jsonl, fan out; 202
GET  /events/stream?session_id=&types=&once=
                                   SSE, `id: <seq>`, replays after Last-Event-ID (header or ?last_event_id=),
                                   `:` heartbeat after 15 s without a write; once=1 replays and closes (curl,
                                   tests). A reader that falls 1000 events behind is closed and reconnects.
GET  /sessions/{id}[?format=html]  that session's events, JSON; html is the read-only page behind the receipt's
                                   QR code (relay/session_view.py), served publicly by the caregiver app at /s/<id>
GET  /jwks.json, /.well-known/jwks.json
GET  /audio/{name}                 refusal clips from ai/warnings/
GET  /wall                         the wall page; /wall/data/{panel,mandate,budget} proxy merchant and policy
POST /reset                        policy and merchant /reset in parallel, then truncate live.jsonl and post a
                                   `reset` event (the station answers with a new session); answers
                                   {ok, policy, merchant, failed, ms}. Needs X-Chaperone-Host: 1 (relay/host.py)
/host, /host/api/*                 the Host's LAN-only controls (relay/host.py)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx
import jsonschema
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from common.config import JWKS_PATH, ROOT, ledger_path
from relay import session_view

SCHEMA = json.loads((ROOT / "contracts" / "events.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
AUDIO_DIR = ROOT / "ai" / "warnings"
WALL_HTML = Path(__file__).with_name("wall.html")
HEARTBEAT_S = 15.0
MANDATE_ID = "m_ruth_2026_09"
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # contracts/events.schema.json; "live" is reserved
QUEUE_MAX = 1000

router = APIRouter()


class Ledger:
    """Append-only JSONL files plus in-process fan-out. One relay process owns the files."""

    def __init__(self, live_path: Path):
        self.live_path = live_path
        self.sessions_dir = live_path.parent / "by-id"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._subscribers: set[asyncio.Queue] = set()
        self.seq = self._last_seq()

    def _last_seq(self) -> int:
        last = 0
        for event in self.read_live():
            last = max(last, int(event.get("seq", 0)))
        return last

    def read_live(self) -> list[dict]:
        return _read_jsonl(self.live_path)

    def session_path(self, session_id: str) -> Path:
        if not SESSION_ID.fullmatch(session_id) or session_id == "live":
            raise ValueError(f"bad session id {session_id!r}")
        return self.sessions_dir / f"{session_id}.jsonl"

    def append(self, event: dict) -> dict:
        line = None
        with self._lock:
            self.seq += 1
            event = {**event, "rt": int(time.time() * 1000), "seq": self.seq}
            line = json.dumps(event, ensure_ascii=False) + "\n"
            for path in (self.session_path(event["session_id"]), self.live_path):
                with path.open("a", encoding="utf-8") as f:
                    f.write(line)
        for sub in list(self._subscribers):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:  # a stuck reader never blocks senders: its stream closes, the client
                sub.overflowed = True  # reconnects with Last-Event-ID and replays from the file
                self._subscribers.discard(sub)
        return event

    def truncate_live(self) -> None:
        with self._lock:
            self.live_path.write_text("", encoding="utf-8")

    def subscribe(self) -> "Subscriber":
        sub = Subscriber(asyncio.Queue(maxsize=QUEUE_MAX))
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: "Subscriber") -> None:
        self._subscribers.discard(sub)


class Subscriber:
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue
        self.overflowed = False


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn last line after a crash must not break replay
    return events


LEDGER = Ledger(ledger_path())


def _matches(event: dict, session_id: str | None, types: set[str] | None) -> bool:
    if session_id and event.get("session_id") != session_id:
        return False
    return not types or event.get("type") in types


def _sse(event: dict) -> str:
    return f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/events", status_code=202)
async def post_event(request: Request):
    try:
        event = await request.json()
    except ValueError:
        raise HTTPException(400, "body is not JSON")
    errors = sorted(VALIDATOR.iter_errors(event), key=lambda e: list(e.path))
    if errors:
        raise HTTPException(422, [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors])
    stored = LEDGER.append(event)
    return {"seq": stored["seq"], "rt": stored["rt"]}


@router.get("/events/stream")
async def stream(
    request: Request,
    session_id: str | None = None,
    types: str | None = None,
    last_event_id: int | None = Query(None),
    once: bool = False,
):
    wanted = {t.strip() for t in types.split(",") if t.strip()} if types else None
    header_id = request.headers.get("last-event-id")
    after = int(header_id) if header_id and header_id.isdigit() else (last_event_id or 0)

    async def events():
        sub = None if once else LEDGER.subscribe()  # subscribe before replay so nothing falls in the gap
        try:
            sent = after
            yield "retry: 2000\n\n"
            for event in LEDGER.read_live():
                if event.get("seq", 0) > sent and _matches(event, session_id, wanted):
                    sent = event["seq"]
                    yield _sse(event)
            if sub is None:
                return
            last_write = time.monotonic()  # a filtered stream may see no matching event for minutes
            while not sub.overflowed:
                wait = max(0.0, HEARTBEAT_S - (time.monotonic() - last_write))
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=wait)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": heartbeat\n\n"
                    last_write = time.monotonic()
                    continue
                if event["seq"] > sent and _matches(event, session_id, wanted):
                    sent = event["seq"]
                    yield _sse(event)
                    last_write = time.monotonic()
        finally:
            if sub is not None:
                LEDGER.unsubscribe(sub)

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/sessions/{session_id}")
async def session_events(session_id: str, format: str = "json"):
    # "none" collects events without a shopper session (webhook rejections, resets); it is not a session page.
    if session_id == "none":
        raise HTTPException(404, "unknown session")
    try:
        path = LEDGER.session_path(session_id)
    except ValueError:
        raise HTTPException(404, "unknown session") from None
    events = _read_jsonl(path)
    if format != "html":
        return events
    if not path.exists():
        raise HTTPException(404, "unknown session")
    return HTMLResponse(session_view.render(session_id, events, await _receipts_for(events)),
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


MAX_RECEIPTS = 6


async def _receipts_for(events: list[dict]) -> list[dict]:
    """The merchant's receipt for each paid order in the session, fetched in parallel. The page renders
    without them if the merchant is slow."""
    order_ids = [o["order_id"] for o in session_view.orders(events)
                 if o["paid"] and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", o["order_id"])][-MAX_RECEIPTS:]
    if not order_ids:
        return []
    base = _service("MERCHANT_URL", "http://127.0.0.1:8002")

    async def one(client: httpx.AsyncClient, order_id: str) -> dict | None:
        try:
            r = await client.get(f"{base}/orders/{order_id}/receipt")
            return r.json() if r.is_success else None
        except (httpx.HTTPError, ValueError):
            return None

    async with httpx.AsyncClient(timeout=0.8) as client:
        return [r for r in await asyncio.gather(*(one(client, o) for o in order_ids)) if r]


@router.get("/jwks.json")
@router.get("/.well-known/jwks.json")
def jwks():
    return JSONResponse(json.loads(JWKS_PATH.read_text(encoding="utf-8")), headers={"Cache-Control": "max-age=60"})


@router.get("/audio/{name}")
def audio(name: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.mp3", name):
        raise HTTPException(404, "not found")
    path = AUDIO_DIR / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/wall", response_class=HTMLResponse)
def wall():
    return HTMLResponse(WALL_HTML.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


def _service(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


WALL_SOURCES = {
    "panel": lambda: f"{_service('MERCHANT_URL', 'http://127.0.0.1:8002')}/panel",
    "mandate": lambda: f"{_service('POLICY_URL', 'http://127.0.0.1:8001')}/mandate",
    "budget": lambda: f"{_service('POLICY_URL', 'http://127.0.0.1:8001')}/budget?mandate_id={MANDATE_ID}",
}


@router.get("/wall/data/{source}")
async def wall_data(source: str):
    """Same-origin proxy so the wall page needs no CORS on the merchant or policy."""
    if source not in WALL_SOURCES:
        raise HTTPException(404, "unknown source")
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get(WALL_SOURCES[source]())
        return JSONResponse(r.json(), status_code=r.status_code, headers={"Cache-Control": "no-store"})
    except (httpx.HTTPError, ValueError) as exc:
        if source == "mandate":  # the wall still shows the rules, labelled as the unsigned default
            from policy.mandate import DEFAULT_MANDATE
            return JSONResponse({"mandate": DEFAULT_MANDATE, "signed": False, "fallback": True})
        return JSONResponse({"unavailable": f"{source}: {type(exc).__name__}"}, status_code=503)


RESET_TIMEOUT_S = 5.0


async def _reset_one(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.post(f"{url}/reset")
        return "ok" if r.is_success else f"HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        return f"unreachable ({type(exc).__name__})"


@router.post("/reset")
async def reset(request: Request):
    """Back to the demo start in well under 15 s. Seq keeps counting so stream readers stay valid.

    Policy resets spend, decisions, approvals and the screen's session memory; the merchant its orders.
    LAN only, like /host/api/reset.
    """
    from relay.host import host_action  # host imports this module; import here to avoid a cycle

    host_action(request)
    started = time.perf_counter()
    services = {"policy": _service("POLICY_URL", "http://127.0.0.1:8001"),
                "merchant": _service("MERCHANT_URL", "http://127.0.0.1:8002")}
    async with httpx.AsyncClient(timeout=RESET_TIMEOUT_S) as client:
        answers = await asyncio.gather(*(_reset_one(client, url) for url in services.values()))
    results = dict(zip(services, answers))
    failed = [name for name, answer in results.items() if answer != "ok"]
    LEDGER.truncate_live()  # after the fan-out, so nothing a service posted while resetting survives it
    ms = round((time.perf_counter() - started) * 1000)
    LEDGER.append({"type": "reset", "session_id": "none", "mandate_id": MANDATE_ID,
                   "t": int(time.time() * 1000), "source": "relay", "results": results, "failed": failed, "ms": ms})
    return {"ok": not failed, **results, "failed": failed, "ms": ms}


from relay.host import router as host_router  # noqa: E402 - host.py uses this module's LEDGER and reset

router.include_router(host_router)

app = FastAPI(title="Chaperone ledger (standalone)")
app.include_router(router)
