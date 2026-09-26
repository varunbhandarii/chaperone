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
    session = [json.loads(line) for line in (tmp_path / "by-id" / "s1.jsonl").read_text().splitlines()]
    assert live == session and live[0]["seq"] == 1 and isinstance(live[0]["rt"], int)
    assert client.post("/events", json=event()).json()["seq"] == 2


@pytest.mark.parametrize("bad", [
    {k: v for k, v in event().items() if k != "source"},          # missing source
    {**event(), "t": "1695600000"},                                # t must be an integer
    {**event(), "type": "made_up"},                                # not in the enum
    {**event(), "source": "someone"},                              # unknown sender
    event(session_id="live"),                                      # reserved for the all-sessions log
    event(session_id="../etc"),                                    # not a session id
    event(session_id="x" * 65),                                    # too long
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
        sub = book.subscribe()
        book.append(event("paid"))
        return await asyncio.wait_for(sub.queue.get(), 1)
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
    assert r["ok"] is False and r["policy"].startswith("unreachable") and r["merchant"].startswith("unreachable")
    assert isinstance(r["ms"], int) and r["ms"] < 15000
    live = stream_events(client)
    assert [e["type"] for e in live] == ["reset"] and live[0]["seq"] == 2  # seq keeps counting
    assert (tmp_path / "by-id" / "s1.jsonl").exists()  # per-session history stays


def test_append_is_fast(tmp_path):
    book = ledger.Ledger(tmp_path / "live.jsonl")
    start = time.perf_counter()
    for _ in range(200):
        book.append(event())
    assert (time.perf_counter() - start) / 200 < 0.005


def test_reset_is_lan_only(client):
    assert client.post("/reset", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 403


def test_events_without_a_session_have_no_page(client):
    client.post("/events", json=event(session_id="none"))
    assert client.get("/sessions/none?format=html").status_code == 404
    assert client.get("/sessions/none").status_code == 404


def test_bad_session_id_is_404(client):
    assert client.get("/sessions/live").status_code == 404
    assert client.get("/sessions/a.b").status_code == 404
    client.post("/events", json=event())
    assert [e["seq"] for e in client.get("/sessions/s1").json()] == [1]


def test_filtered_stream_still_gets_heartbeats(tmp_path, monkeypatch):
    """The phone's stream filters by session; other sessions' events must not starve its heartbeat."""
    monkeypatch.setattr(ledger, "HEARTBEAT_S", 0.2)
    book = ledger.Ledger(tmp_path / "live.jsonl")
    monkeypatch.setattr(ledger, "LEDGER", book)

    class Req:
        headers: dict = {}

        async def is_disconnected(self):
            return False

    async def run():
        response = await ledger.stream(Req(), session_id="phone", types=None, last_event_id=None, once=False)
        chunks = response.body_iterator
        assert (await anext(chunks)).startswith("retry")
        async def noise():
            for _ in range(8):
                book.append(event(session_id="other"))
                await asyncio.sleep(0.05)
        task = asyncio.create_task(noise())
        got = await asyncio.wait_for(anext(chunks), 1)
        await task
        await chunks.aclose()
        return got
    assert asyncio.run(run()) == ": heartbeat\n\n"


def test_overflowing_reader_is_closed_to_reconnect(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "QUEUE_MAX", 3)
    book = ledger.Ledger(tmp_path / "live.jsonl")

    async def run():
        sub = book.subscribe()
        for _ in range(5):
            book.append(event())
        return sub
    sub = asyncio.run(run())
    assert sub.overflowed and sub not in book._subscribers
    assert len(book.read_live()) == 5  # nothing lost: the reconnect replays from the file


def test_session_page_shows_one_line_per_turn_rules_checks_and_payment(client):
    client.post("/events", json=event("heard", role="shopper", text="necesito", item_id="t1", lang="es"))
    client.post("/events", json=event("heard", role="shopper", text="necesito pan y mi medicina", item_id="t1"))
    client.post("/events", json=event("heard", role="agent", text="<script>alert(1)</script>", item_id="t2"))
    client.post("/events", json={**event("policy_decision", decision="allow", decision_id="d_1",
                                         rules_failed=[], total=11.49), "source": "policy"})
    checks = [{"id": c, "passed": True, "detail": "ok"} for c in ("content_digest", "signature", "window", "nonce", "decision")]
    client.post("/events", json={**event("signature_verified", keyid="chaperone-agent-1", nonce="n-123",
                                         checks=checks, decision_id="d_1"), "source": "merchant"})
    client.post("/events", json={**event("signature_rejected", decision_id="d_1", checks=[
        {"id": "decision", "passed": False, "detail": "decision d_1 already has order ord_1"}]), "source": "merchant"})
    client.post("/events", json={**event("paid", order_id="ord_1", total="11.49", via="host"), "source": "merchant"})
    r = client.get("/sessions/s1", params={"format": "html"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    page = r.text
    assert page.count("necesito") == 1 and "necesito pan y mi medicina" in page
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "ALLOW · every rule passed" in page and "5 of 5 checks passed" in page
    assert "n-123" in page and "Paid $11.49" in page
    assert "Merchant rejected the request" in page  # the replayed order is listed, the card shows the pass
    assert client.get("/sessions/nobody", params={"format": "html"}).status_code == 404
    assert client.get("/sessions/s1").json()[0]["type"] == "heard"  # JSON stays the default


def test_session_page_loads_fast_with_the_merchant_down(client):
    for i in range(300):
        client.post("/events", json=event("heard", text=f"turn {i}", item_id=f"t{i}"))
    client.post("/events", json={**event("paid", order_id="ord_1", total="11.49"), "source": "merchant"})
    started = time.perf_counter()
    assert client.get("/sessions/s1", params={"format": "html"}).status_code == 200
    assert time.perf_counter() - started < 2
