import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from relay import ledger


def event(type_="heard", session_id="s1", **extra):
    return {"type": type_, "session_id": session_id, "mandate_id": "m_ruth_2026_09",
            "t": int(time.time() * 1000), "source": "station", **extra}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER", ledger.Ledger(tmp_path / "live.jsonl"))
    monkeypatch.setenv("POLICY_URL", "http://127.0.0.1:9")  # nothing listens: services "offline"
    monkeypatch.setenv("MERCHANT_URL", "http://127.0.0.1:9")
    return TestClient(ledger.app)


def stream_events(client, **params):
    body = client.get("/events/stream", params={"once": 1, **params}).text
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_post_appends_with_seq_and_rt(client, tmp_path):
    r = client.post("/events", json=event(text="necesito pan"))
    assert r.status_code == 202 and r.json()["seq"] == 1
    live = [json.loads(line) for line in (tmp_path / "live.jsonl").read_text().splitlines()]
    session = [json.loads(line) for line in (tmp_path / "s1.jsonl").read_text().splitlines()]
    assert live == session and live[0]["seq"] == 1 and isinstance(live[0]["rt"], int)
    assert client.post("/events", json=event()).json()["seq"] == 2


@pytest.mark.parametrize("bad", [
    {k: v for k, v in event().items() if k != "source"},          # missing source
    {**event(), "t": "1695600000"},                                # t must be an integer
    {**event(), "type": "made_up"},                                # not in the enum
    {**event(), "source": "someone"},                              # unknown sender
])
def test_invalid_events_are_rejected(client, bad):
    assert client.post("/events", json=bad).status_code == 422


def test_seq_continues_after_restart(tmp_path):
    first = ledger.Ledger(tmp_path / "live.jsonl")
    first.append(event())
    first.append(event())
    assert ledger.Ledger(tmp_path / "live.jsonl").append(event())["seq"] == 3


def test_stream_replays_filtered_and_after_last_event_id(client):
    for e in (event("heard"), event("refusal", rule_id="R1"), event("heard", session_id="s2"), event("paid")):
        client.post("/events", json=e)
    assert [e["seq"] for e in stream_events(client)] == [1, 2, 3, 4]
    assert [e["type"] for e in stream_events(client, types="refusal,paid")] == ["refusal", "paid"]
    assert [e["seq"] for e in stream_events(client, session_id="s2")] == [3]
    assert [e["seq"] for e in stream_events(client, last_event_id=2)] == [3, 4]
    body = client.get("/events/stream", params={"once": 1}, headers={"Last-Event-ID": "3"}).text
    assert "id: 4\n" in body and "id: 3\n" not in body


def test_stream_headers(client):
    r = client.get("/events/stream", params={"once": 1})
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache, no-transform"


def test_live_subscribers_get_new_events(tmp_path):
    async def run():
        book = ledger.Ledger(tmp_path / "live.jsonl")
        queue = book.subscribe()
        book.append(event("paid"))
        return await asyncio.wait_for(queue.get(), 1)
    assert asyncio.run(run())["type"] == "paid"


def test_session_page(client):
    client.post("/events", json=event(session_id="s9"))
    client.post("/events", json=event(session_id="s8"))
    assert [e["session_id"] for e in client.get("/sessions/s9").json()] == ["s9"]
    assert client.get("/sessions/unknown").json() == []


def test_session_ids_cannot_escape_the_sessions_dir(client, tmp_path):
    client.post("/events", json=event(session_id="../../etc/passwd"))
    assert all(p.parent == tmp_path for p in tmp_path.rglob("*.jsonl"))


def test_jwks_on_both_paths(client):
    a, b = client.get("/jwks.json").json(), client.get("/.well-known/jwks.json").json()
    assert a == b and a["keys"][0]["kid"] == "chaperone-agent-1"


def test_audio_serves_clips_only(client):
    clips = sorted(ledger.AUDIO_DIR.glob("*.mp3"))
    assert clips, "ai/warnings has no clips"
    r = client.get(f"/audio/{clips[0].name}")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert client.get("/audio/..%2F..%2F.env").status_code == 404
    assert client.get("/audio/missing.mp3").status_code == 404


def test_wall_page_and_offline_fallbacks(client):
    assert "Chaperone" in client.get("/wall").text
    mandate = client.get("/wall/data/mandate").json()
    assert mandate["fallback"] and mandate["mandate"]["mandate_id"] == "m_ruth_2026_09"
    assert client.get("/wall/data/panel").status_code == 503
    assert client.get("/wall/data/nope").status_code == 404


def test_reset_truncates_and_reports(client, tmp_path):
    client.post("/events", json=event())
    r = client.post("/reset").json()
    assert r["ok"] and r["policy"].startswith("unreachable") and r["merchant"].startswith("unreachable")
    live = stream_events(client)
    assert [e["type"] for e in live] == ["reset"] and live[0]["seq"] == 2  # seq keeps counting
    assert (tmp_path / "s1.jsonl").exists()  # per-session history stays


def test_append_is_fast(tmp_path):
    book = ledger.Ledger(tmp_path / "live.jsonl")
    start = time.perf_counter()
    for _ in range(200):
        book.append(event())
    assert (time.perf_counter() - start) / 200 < 0.005
