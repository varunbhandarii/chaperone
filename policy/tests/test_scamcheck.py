import time

import pytest
from fastapi.testclient import TestClient

from policy import risk, scamcheck
from policy.approvals import action_marker
from policy.main import app

GRANDPARENT = ("My grandson Alex called crying, he's in jail and needs $2,000 for bail today, "
               "and he said don't tell his mom.")
POWER = "Peachtree Power called and said they will cut my power tonight unless I pay $480 in gift cards."
EVENTS: list[tuple] = []


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name, var in (("decisions.json", "DECISIONS_PATH"), ("monthly.json", "MONTHLY_PATH"),
                      ("mandate.json", "MANDATE_PATH"), ("risk.json", "RISK_PATH"),
                      ("radar_cache.json", "RADAR_CACHE_PATH"), ("scam_checks.json", "SCAM_CHECKS_PATH")):
        monkeypatch.setenv(var, str(tmp_path / name))
    monkeypatch.setenv("RADAR_FAKE", "1")
    monkeypatch.setenv("POLICY_DEV_KEY_OK", "1")
    monkeypatch.setenv("RELAY_URL", "")
    monkeypatch.setattr(scamcheck, "_biller_account", lambda m, a: {
        "biller": "Peachtree Power", "balance_due": "86.40", "due_date": "2026-10-15", "past_due": False,
        "disconnect_notice": False})
    EVENTS.clear()
    record = lambda t, s, m, **f: EVENTS.append((t, f))  # noqa: E731
    monkeypatch.setattr(scamcheck, "post_event", record)
    monkeypatch.setattr(risk, "post_event", record)
    from policy.screen import reset_sessions
    reset_sessions()


def client():
    return TestClient(app)


def verdict_from_grok(**over):
    v = {"verdict": "scam", "pattern": "utility_shutoff", "say": "Ruth, your real bill is paid. Please hang up.",
         "actions": ["hang_up"], "reported_recently": "FTC warns of utility shut-off calls.",
         "sources": [{"title": "FTC: utility scams", "url": "https://consumer.ftc.gov/utility"}]}
    v.update(over)
    return v


# ---------------------------------------------------------------- rules first

def test_hard_rule_answers_at_once_and_sets_the_cooldown():
    start = time.perf_counter()
    out = client().post("/scam-check", json={"story": GRANDPARENT, "lang": "en", "session_id": "s1"}).json()
    assert (time.perf_counter() - start) < 0.5
    assert out["verdict"] == "scam" and out["pattern"] == "grandparent_emergency"
    assert out["say"] == scamcheck.lines("en")["scam_check_scam"]
    assert "tell_priya" in out["actions"]
    assert out["cooldown_until"] and risk.load_risk("m_ruth_2026_09")["active"]
    assert [t for t, _ in EVENTS][:2] == ["risk_changed", "scam_checked"]


def test_hard_rule_uses_the_cached_grok_answer_when_there_is_one():
    scamcheck.cache_put(verdict_from_grok(pattern="grandparent_emergency", say="Cached line."),
                        "pattern:grandparent_emergency:en")
    out = scamcheck.check(GRANDPARENT, "en")
    assert out["say"] == "Cached line." and out["from_cache"] and out["sources"]


def test_scam_alerts_priya_with_the_check_id():
    out = scamcheck.check(GRANDPARENT, "es")
    deadline = time.time() + 2
    while not any(t == "caregiver_alerted" for t, _ in EVENTS) and time.time() < deadline:
        time.sleep(0.02)
    alert = next(f for t, f in EVENTS if t == "caregiver_alerted")
    assert alert["check_id"] == out["check_id"] and alert["kind"] == "scam_check"


# ---------------------------------------------------------------- Grok, cache, fallback

def test_grok_verdict_is_used_and_cached(monkeypatch):
    monkeypatch.setattr(scamcheck, "radar", lambda *a, **k: verdict_from_grok())
    out = scamcheck.check("A man from the power company says I owe money and must pay today.", "en")
    assert out["verdict"] == "scam" and out["sources"] and not out["from_cache"]
    assert scamcheck.cache_get("pattern:utility_shutoff:en")


def test_cache_answers_when_grok_is_down():
    story = "A man from the power company says I owe money and must pay today."
    scamcheck.cache_put(verdict_from_grok(), scamcheck.story_key(story, "en"))
    out = scamcheck.check(story, "en")
    assert out["from_cache"] and out["verdict"] == "scam" and out["say"].startswith("Ruth, your real bill")


def test_rules_only_fallback_without_grok_or_cache():
    out = scamcheck.check("Someone called about my account, I'm not sure what they wanted.", "hi")
    assert out["verdict"] == "unsure" and out["say"] == scamcheck.lines("hi")["scam_check_unsure"]
    assert out["cooldown_until"] is None and not risk.load_risk()["active"]


def test_ok_verdict_sets_no_cooldown(monkeypatch):
    monkeypatch.setattr(scamcheck, "radar", lambda *a, **k: verdict_from_grok(verdict="ok", pattern="none", actions=["none"]))
    out = scamcheck.check("My son Priyank is coming for dinner, is that fine?", "en")
    assert out["verdict"] == "ok" and out["cooldown_until"] is None and out["actions"] == []


# ---------------------------------------------------------------- facts

def test_facts_compare_the_caller_with_the_saved_number():
    facts = scamcheck.gather_facts("m", GRANDPARENT, {"name": "Alex", "phone": "+1 (678) 555-0100"})
    assert {"fact": "Alex's number on file (grandson)", "result": "+1-404-555-0187, different from the caller"} in facts


def test_facts_read_the_real_power_bill():
    facts = scamcheck.gather_facts("m", POWER, {})
    assert any(f["result"].startswith("$86.40 due 2026-10-15, not past due") for f in facts)


def test_call_trusted_names_the_contact(monkeypatch):
    monkeypatch.setattr(scamcheck, "radar", lambda *a, **k: verdict_from_grok(actions=["hang_up", "call_trusted"]))
    out = scamcheck.check("My grandson Alex says he needs money, is this real?", "en")
    assert "call_trusted:Alex" in out["actions"]


# ---------------------------------------------------------------- routes, risk, reset

def test_get_check_returns_the_stored_check():
    out = client().post("/scam-check", json={"story": POWER, "lang": "en"}).json()
    got = client().get(f"/scam-check/{out['check_id']}").json()
    assert got["check_id"] == out["check_id"] and got["story"].startswith("Peachtree Power")
    assert client().get("/scam-check/sc_nope").status_code == 404


def test_cooldown_extends_but_never_shortens():
    first = risk.set_cooldown("m", 24, "a")["cooldown_until"]
    assert risk.set_cooldown("m", 1, "b")["cooldown_until"] == first


def test_risk_routes_and_caregiver_only_clear():
    risk.set_cooldown("m_ruth_2026_09", 24, "grandparent_emergency", "sc_1")
    assert client().get("/risk?mandate_id=m_ruth_2026_09").json()["active"]
    assert client().post("/risk/clear", json={"mandate_id": "m_ruth_2026_09"}).status_code == 401
    marker = action_marker("mandate", "risk")
    cleared = client().post("/risk/clear", json={"mandate_id": "m_ruth_2026_09"}, headers={"x-chaperone-marker": marker})
    assert cleared.status_code == 200 and not cleared.json()["active"]


def test_reset_clears_risk_and_checks():
    from policy.store import reset

    scamcheck.check(GRANDPARENT, "en")
    reset()
    assert not risk.load_risk()["active"] and not scamcheck._read(scamcheck.checks_path())


def test_every_language_has_the_fallback_lines():
    for lang in ("en", "es", "hi"):
        assert {"scam_check_scam", "scam_check_unsure", "scam_check_ok"} <= set(scamcheck.lines(lang))


# ---------------------------------------------------------------- transcript and the phone line

def test_transcript_is_screened_even_when_the_summary_hides_it():
    out = client().post("/scam-check", json={
        "story": "Ruth got a call from someone saying they are family and need help.",
        "transcript": "He said he's my grandson Alex, he's in jail, needs $2,000 bail and don't tell his mom.",
        "lang": "en", "channel": "line"}).json()
    assert out["verdict"] == "scam" and out["pattern"] == "grandparent_emergency"
    stored = client().get(f"/scam-check/{out['check_id']}").json()
    assert stored["channel"] == "line" and stored["transcript"].startswith("He said he's my grandson")


def test_transcript_alone_is_enough_and_an_empty_body_is_refused():
    assert client().post("/scam-check", json={"transcript": GRANDPARENT}).json()["verdict"] == "scam"
    assert client().post("/scam-check", json={"lang": "en"}).status_code == 422


def test_grok_gets_the_exact_words(monkeypatch):
    seen = {}

    def fake_radar(story, lang, caller, facts, hints, timeout, transcript=""):
        seen.update(story=story, transcript=transcript)
        return verdict_from_grok(verdict="ok", pattern="none", actions=["none"])

    monkeypatch.setattr(scamcheck, "radar", fake_radar)
    scamcheck.check("A caller asked about the power bill.", "en", transcript="They said the power bill is fine.")
    assert seen == {"story": "A caller asked about the power bill.", "transcript": "They said the power bill is fine."}
