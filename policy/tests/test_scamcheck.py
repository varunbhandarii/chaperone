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
    # her own contacts answer the story: call Alex back at the number she has saved
    assert out["say"] == scamcheck.lines("en")["scam_check_family"].format(name="Alex")
    assert "tell_priya" in out["actions"]
    assert out["cooldown_until"] and risk.load_risk("m_ruth_2026_09")["active"]
    assert [t for t, _ in EVENTS][:2] == ["risk_changed", "scam_checked"]


def test_hard_rule_uses_the_cached_answer_for_the_same_story():
    scamcheck.remember(verdict_from_grok(pattern="grandparent_emergency", say="Cached line."), GRANDPARENT, "en",
                       "grandparent_emergency")
    out = scamcheck.check(GRANDPARENT, "en")
    assert out["say"] == "Cached line." and out["from_cache"] and out["sources"]


def test_pattern_cache_gives_sources_but_never_another_storys_words():
    scamcheck.remember(verdict_from_grok(pattern="grandparent_emergency", say="Alex is not in jail, call him."),
                       "a different grandparent story about Alex", "en", "grandparent_emergency")
    out = scamcheck.check(GRANDPARENT.replace("Alex", "Sam"), "en")
    assert out["from_cache"] and out["sources"] and "Alex" not in out["say"]
    assert out["say"] == scamcheck.lines("en")["scam_check_scam"]


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


def test_facts_say_when_the_callers_number_is_unknown():
    facts = scamcheck.gather_facts("m", GRANDPARENT, {})
    assert {"fact": "Alex's number on file (grandson)", "result": "+1-404-555-0187; the caller's number is unknown"} in facts


@pytest.mark.parametrize("lang", ["en", "es", "hi"])
def test_a_paid_bill_answers_the_power_company_call(monkeypatch, lang):
    monkeypatch.setattr(scamcheck, "_biller_account", lambda m, a: {
        "biller": "Peachtree Power", "balance_due": "0.00", "due_date": "2026-10-15", "past_due": False,
        "disconnect_notice": False})
    out = scamcheck.check(POWER, lang)
    assert out["verdict"] == "scam" and out["say"] == scamcheck.lines(lang)["scam_check_bill_paid"].format(biller="Peachtree Power")


def test_a_bill_with_money_due_keeps_the_plain_scam_line():
    out = scamcheck.check(POWER, "en")  # $86.40 due: the threat is still a scam, but "your bill is paid" would be false
    assert out["say"] == scamcheck.lines("en")["scam_check_scam"]


def test_facts_read_the_real_power_bill():
    facts = scamcheck.gather_facts("m", POWER, {})
    assert any(f["result"].startswith("$86.40 due 2026-10-15, not past due") for f in facts)


def test_call_trusted_names_the_contact(monkeypatch):
    monkeypatch.setattr(scamcheck, "radar", lambda *a, **k: verdict_from_grok(actions=["hang_up", "call_trusted"]))
    out = scamcheck.check("My grandson Alex says he needs money, is this real?", "en")
    assert "call_trusted:Alex" in out["actions"]


# ---------------------------------------------------------------- routes, risk, reset

def view(check_id):
    return {"x-chaperone-marker": action_marker(check_id, "view")}


def test_get_check_needs_priyas_marker():
    out = client().post("/scam-check", json={"story": POWER, "lang": "en"}).json()
    assert client().get(f"/scam-check/{out['check_id']}").status_code == 401
    got = client().get(f"/scam-check/{out['check_id']}", headers=view(out["check_id"])).json()
    assert got["check_id"] == out["check_id"] and got["story"].startswith("Peachtree Power")
    assert client().get("/scam-check/sc_nope", headers=view("sc_nope")).status_code == 404


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
    stored = client().get(f"/scam-check/{out['check_id']}", headers=view(out["check_id"])).json()
    assert stored["channel"] == "line" and stored["transcript"].startswith("He said he's my grandson")


def test_transcript_alone_is_enough_and_an_empty_body_is_refused():
    assert client().post("/scam-check", json={"transcript": GRANDPARENT}).json()["verdict"] == "scam"
    assert client().post("/scam-check", json={"lang": "en"}).status_code == 422


def test_grok_gets_the_exact_words(monkeypatch):
    seen = {}

    def fake_radar(story, lang, caller, facts, hints, timeout, transcript="", on_late=None):
        seen.update(story=story, transcript=transcript)
        return verdict_from_grok(verdict="ok", pattern="none", actions=["none"])

    monkeypatch.setattr(scamcheck, "radar", fake_radar)
    scamcheck.check("A caller asked about the power bill.", "en", transcript="They said the power bill is fine.")
    assert seen == {"story": "A caller asked about the power bill.", "transcript": "They said the power bill is fine."}


# ---------------------------------------------------------------- budget, say length, amount

def test_the_whole_check_stays_inside_its_budget(monkeypatch):
    monkeypatch.setenv("RADAR_BUDGET_S", "0.8")

    def slow_facts(*a):
        time.sleep(3)
        return [{"fact": "late", "result": "late"}]

    seen = {}

    def fake_radar(story, lang, caller, facts, hints, timeout, transcript="", on_late=None):
        seen.update(facts=facts, timeout=timeout)
        raise scamcheck.RadarError("slow")

    monkeypatch.setattr(scamcheck, "gather_facts", slow_facts)
    monkeypatch.setattr(scamcheck, "radar", fake_radar)
    start = time.perf_counter()
    scamcheck.check("Someone from the bank says my account has a problem.", "en")
    assert time.perf_counter() - start < 1.2
    assert seen["facts"] == [] and seen["timeout"] < 0.8


def test_a_late_grok_answer_is_cached_for_next_time(monkeypatch):
    story = "Someone from the bank says my account has a problem."
    pending = []

    def fake_radar(story, lang, caller, facts, hints, timeout, transcript="", on_late=None):
        pending.append(on_late)
        raise scamcheck.RadarError("late")

    monkeypatch.setattr(scamcheck, "radar", fake_radar)
    first = scamcheck.check(story, "en")
    pending[0](verdict_from_grok(pattern="bank_impersonation", say="That is not your bank. Please hang up."))
    monkeypatch.setattr(scamcheck, "radar", lambda *a, **k: (_ for _ in ()).throw(scamcheck.RadarError("down")))
    second = scamcheck.check(story, "en")
    assert first["verdict"] == "unsure" and second["from_cache"] and second["say"] == "That is not your bank. Please hang up."


@pytest.mark.parametrize("say,ok", [
    ("This is a scam. Please hang up.", True),
    ("Esto es una estafa. Por favor cuelgue.", True),
    ("यह धोखा है। कृपया फ़ोन रख दीजिए।", True),
    ("One. Two. Three sentences is too many.", False),
    (" ".join(["word"] * 31) + ".", False),
    ("", False),
])
def test_say_is_two_short_sentences(say, ok):
    assert scamcheck.say_ok(say) is ok


def test_a_long_say_falls_back_to_the_fixed_line_and_keeps_sources(monkeypatch):
    long_say = "This is a scam. Your bill is paid. Please hang up and call Priyank."
    monkeypatch.setattr(scamcheck, "radar", lambda *a, **k: verdict_from_grok(say=long_say))
    out = scamcheck.check("A man says there is a problem with my power account.", "en")
    assert out["say"] == scamcheck.lines("en")["scam_check_scam"] and out["sources"]


@pytest.mark.parametrize("text,amount", [
    ("He needs $2,000 for bail", 2000.0), ("pay $480 in gift cards", 480.0),
    ("necesita 2000 dólares para la fianza", 2000.0), ("quinientos dólares en tarjetas", 500.0),
    ("two thousand dollars", 2000.0), ("दो हज़ार डॉलर चाहिए", 2000.0), ("paanch sau dollar", 500.0),
    ("my grandson is visiting", None),
    ("two thousand five hundred dollars", 2500.0), ("tres mil quinientos dólares", 3500.0),
    ("दो हज़ार पांच सौ डॉलर", 2500.0), ("cuarenta y cinco mil", 45000.0), ("a thousand dollars in gift cards", 1000.0),
    ("two hundred and fifty dollars", 250.0), ("do hazaar paanch sau", 2500.0), ("2 thousand dollars", 2000.0),
    ("I told him a hundred times, it's $50", 50.0), ("do not tell mom", None), ("my two grandsons", None),
])
def test_amount_is_read_from_the_story(text, amount):
    assert scamcheck.extract_amount(text) == amount


def test_amount_is_on_the_check_and_the_event():
    out = scamcheck.check(GRANDPARENT, "en")
    assert out["amount"] == 2000.0
    assert next(f for t, f in EVENTS if t == "scam_checked")["amount"] == 2000.0


# ---------------------------------------------------------------- one alert, a real "Why?"

def test_every_check_is_a_decision_priya_can_ask_about(monkeypatch):
    from policy.postpurchase import explain_decision
    from policy.store import get_decision

    monkeypatch.setenv("EXPLAIN_FAKE", "1")
    out = scamcheck.check(GRANDPARENT, "en")
    doc = get_decision(out["decision_id"])
    assert doc["decision"] == "deny" and doc["source"] == "scam_check" and doc["check_id"] == out["check_id"]
    assert doc["ruth_said"].startswith("My grandson Alex") and doc["scam_check"]["pattern"] == "grandparent_emergency"
    assert explain_decision(out["decision_id"])["headline"]
    assert next(f for t, f in EVENTS if t == "scam_checked")["decision_id"] == out["decision_id"]


def test_the_alert_carries_the_decision_id():
    out = scamcheck.check(GRANDPARENT, "en")
    deadline = time.time() + 2
    while not any(t == "caregiver_alerted" for t, _ in EVENTS) and time.time() < deadline:
        time.sleep(0.02)
    alerts = [f for t, f in EVENTS if t == "caregiver_alerted"]
    assert len(alerts) == 1 and alerts[0]["decision_id"] == out["decision_id"]


def test_unsure_and_ok_checks_are_never_stored_as_allow(monkeypatch):
    from policy.store import get_decision

    unsure = scamcheck.check("Someone called about my account.", "en")
    monkeypatch.setattr(scamcheck, "radar", lambda *a, **k: verdict_from_grok(verdict="ok", pattern="none", actions=["none"]))
    ok = scamcheck.check("Priyank is coming for dinner.", "en")
    assert get_decision(unsure["decision_id"])["decision"] == "caution"
    assert get_decision(ok["decision_id"])["decision"] == "noted"


def test_priyas_safety_list_needs_her_marker_and_is_newest_first():
    first = scamcheck.check(GRANDPARENT, "en")
    second = scamcheck.check(POWER, "en")
    assert client().get("/scam-checks").status_code == 401
    rows = client().get("/scam-checks?mandate_id=m_ruth_2026_09",
                        headers={"x-chaperone-marker": action_marker("scam_checks", "list")}).json()
    assert [r["check_id"] for r in rows][:2] == [second["check_id"], first["check_id"]]
    assert set(rows[0]) == {"check_id", "decision_id", "verdict", "pattern", "story_excerpt", "say", "sources",
                            "amount", "at", "channel"}
