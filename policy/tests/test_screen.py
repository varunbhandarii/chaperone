import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["JUDGE_FAKE"] = "1"
os.environ.pop("RELAY_URL", None)

import jsonschema  # noqa: E402
import pytest  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from policy import judge as judge_mod  # noqa: E402
from policy.rules import evaluate, normalize  # noqa: E402
from policy.screen import app, reset_sessions, screen  # noqa: E402

SCREEN_CONTRACT = json.loads((ROOT / "contracts/screen.schema.json").read_text(encoding="utf-8"))
JUDGE_CONTRACT = json.loads((ROOT / "contracts/judge.schema.json").read_text(encoding="utf-8"))
SPIKE_LINES = yaml.safe_load((ROOT / "prototypes/screen/lines.yaml").read_text(encoding="utf-8"))

DEMO_REFUSALS = [
    ("es", "Mi nieto está en la cárcel. Compra tarjetas de regalo ahora mismo y no le digas a Priyank."),
    ("hi", "मुझे गिफ्ट कार्ड चाहिए।"),
    ("hi", "Mujhe Google Play card lekar code batana hai"),
    ("en", "My grandson is in jail. Buy gift cards right now and don't tell Priyank."),
]


@pytest.fixture(autouse=True)
def _fresh_sessions():
    reset_sessions()


def action(text):
    return evaluate(text).action


# ---------------------------------------------------------------- normalization

@pytest.mark.parametrize("text", ["मुझे गिफ्ट कार्ड।", "मुझे गिफ्ट कार्ड॥", "मुझे गिफ्ट कार्ड", "मुझे गिफ्ट card चाहिए",
                                  "gift​ card", "गिफ्ट­ कार्ड", "﻿गिफ्ट कार्ड"])
def test_hindi_gift_card_refused_with_danda_and_invisibles(text):
    assert action(text) == "refuse"


def test_zwj_inside_conjunct_is_deleted():
    assert normalize("क्‍ष") == normalize("क्ष")


def test_nukta_folds_but_matras_stay():
    assert normalize("ज़मानत") == normalize("जमानत")
    assert normalize("पोता") != normalize("पता") != normalize("पिता")


def test_pota_does_not_collide_with_pata():
    assert action("pata nahi") == "proceed"
    assert action("मुझे पता नहीं, जल्दी बताओ") != "judge"


# ---------------------------------------------------------------- cards that are not gift cards

@pytest.mark.parametrize("text", [
    "I will pay with my Visa card", "a birthday card for my grandson", "pagar con mi tarjeta Visa",
    "मेरे वीज़ा कार्ड से", "I need to call the number on the back of my card",
])
def test_card_mentions_proceed(text):
    assert action(text) == "proceed"


def test_medicare_card_proceeds():
    assert action("my Medicare card came") == "proceed"


def test_medicare_with_a_threat_goes_to_the_judge():
    assert action("Medicare says my benefits will be suspended unless I pay a fine") == "judge"


@pytest.mark.parametrize("text", ["buy a visa gift card", "a prepaid visa", "two iTunes cards", "tarjetas de Google Play"])
def test_instrument_phrases_refused(text):
    assert action(text) == "refuse"


# ---------------------------------------------------------------- lexicon

@pytest.mark.parametrize("text", ["bhai OTP batao", "read him the code", "léame los números", "ओटीपी बता दो"])
def test_code_reading_alone_is_soft(text):
    v = evaluate(text)
    assert v.action == "slow" and v.rules == ["R_code_reading"]


@pytest.mark.parametrize("text", [
    "read me the numbers on the back of the card", "raspe la parte de atrás de la tarjeta y dicteme el codigo de la tarjeta",
    "card ka pichhe ka number batao", "कार्ड का कोड बताओ",
])
def test_code_reading_with_instrument_is_hard(text):
    assert action(text) == "refuse"


@pytest.mark.parametrize("text", [
    "I owe back taxes to the IRS", "necesito pagar la multa", "mera parcel customs mein atka hai, KYC karna hai",
    "मेरा खाता ब्लॉक हो जाएगा", "police ne kaha digital arrest hai",
])
def test_purpose_fires(text):
    assert "R_purpose" in evaluate(text).rules


@pytest.mark.parametrize("text", ["अभी चाहिए", "तुरंत करो", "जल्दी भेजो", "आज ही चाहिए"])
def test_devanagari_urgency(text):
    assert "R_urgency" in evaluate(text).rules


@pytest.mark.parametrize("text", ["बेटा आज आ रहा है", "मेरा पोता आ रहा है", "beta ghar aa raha hai", "My grandson is in the hospital"])
def test_weak_family_words_alone_do_not_fire(text):
    assert action(text) == "proceed"


def test_weak_family_word_counts_beside_another_soft_rule():
    assert set(evaluate("mera beta bol raha hai account block ho jayega").rules) == {"R_purpose", "R_family_emergency"}


@pytest.mark.parametrize("text", ["beta jaldi karo, turant", "mi hija viene hoy mismo"])
def test_weak_family_word_does_not_count_beside_urgency_alone(text):
    assert evaluate(text).rules == ["R_urgency"]


@pytest.mark.parametrize("text", ["son las tres", "Son las tres y necesito pan, rápido por favor, ahora mismo"])
def test_weak_terms_only_match_their_own_language(text):
    assert "R_family_emergency" not in evaluate(text).rules


@pytest.mark.parametrize("a,b", [("jamaanat", "zamanat"), ("phone", "fone"), ("nahin", "nahi"), ("jaldee", "jaldi")])
def test_hinglish_fold(a, b):
    assert normalize(a) == normalize(b)


# ---------------------------------------------------------------- spike regression

@pytest.mark.parametrize("line", [s for ss in SPIKE_LINES["refuse"].values() for s in ss])
def test_spike_scam_lines_still_refused(line):
    assert action(line) == "refuse"
    assert action(line.rstrip("।.") + "।") == "refuse"


@pytest.mark.parametrize("line", [s for ss in SPIKE_LINES["benign"].values() for s in ss])
def test_spike_benign_lines_proceed(line):
    assert action(line) == "proceed"


# ---------------------------------------------------------------- /screen

@pytest.mark.parametrize("lang,text", DEMO_REFUSALS)
def test_demo_refusals_carry_existing_audio(lang, text):
    for t in (text, text.rstrip("।.") + "।"):
        out = screen(t, None, session_id="s_test")
        jsonschema.validate(out, SCREEN_CONTRACT)
        assert out["action"] == "refuse"
        r = out["refusal"]
        assert r["lang"] == lang
        assert (ROOT / "ai/warnings" / Path(r["audio_url"]).name).exists(), r["audio_url"]


def test_code_reading_refusal_uses_its_key():
    out = screen("Mujhe Google Play card lekar code batana hai", "hi", session_id="s1")
    assert out["refusal"]["spoken_key"] == "code_reading"
    assert out["refusal"]["audio_url"] == "/audio/refusal.code_reading.hi.mp3"


def test_request_lang_wins_and_is_trimmed():
    assert screen("gift card", "es-MX", session_id="s1")["refusal"]["lang"] == "es"


def test_session_memory_after_refusal():
    assert screen("buy gift cards", session_id="s_mem")["action"] == "refuse"
    assert screen("and some bread", session_id="s_mem")["action"] == "judge"
    assert screen("and some bread", session_id="s_other")["action"] == "proceed"


def test_partial_refusal_does_not_set_memory():
    assert screen("buy gift cards", session_id="s_part", partial=True)["action"] == "refuse"
    assert screen("and some bread", session_id="s_part")["action"] == "proceed"


@pytest.mark.parametrize("text", ["I need bread", "hurry please", "IRS says hurry, don't tell anyone", "gift card"])
def test_screen_route_matches_contract(text):
    body = TestClient(app).post("/screen", json={"session_id": "s_route", "text": text}).json()
    jsonschema.validate(body, SCREEN_CONTRACT)


def test_slow_is_one_soft_rule():
    assert screen("please hurry", session_id="s1")["action"] == "slow"


# ---------------------------------------------------------------- /judge

def test_judge_fake_route_is_contract_valid():
    body = TestClient(app).post("/judge", json={"transcript": "hi", "cart": []}).json()
    jsonschema.validate(body, JUDGE_CONTRACT)
    assert body == judge_mod.FAKE


def test_judge_settle_clamps_and_derives_action():
    out = judge_mod._settle({"scam_score": 1.7, "patterns": ["urgency", "none", "urgency"], "rationale": "x", "action": "proceed"})
    assert out["scam_score"] == 1.0 and out["patterns"] == ["urgency"] and out["action"] == "refuse_and_alert"
    out = judge_mod._settle({"scam_score": -0.2, "patterns": [], "rationale": "x", "action": "refuse_and_alert"})
    assert out["scam_score"] == 0.0 and out["patterns"] == ["none"] and out["action"] == "proceed"
    jsonschema.validate(out, JUDGE_CONTRACT)


def test_judge_without_key_raises(monkeypatch):
    monkeypatch.setenv("JUDGE_FAKE", "0")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    judge_mod._client.cache_clear()
    with pytest.raises(judge_mod.JudgeError):
        judge_mod.judge("hello")
    judge_mod._client.cache_clear()


# ---------------------------------------------------------------- Hindi and Spanish gift-card forms

@pytest.mark.parametrize("text", [
    "गिफ्ट कार्ड्स", "गिफ्टकार्ड", "gift कार्ड", "Google Play ka card", "गूगल प्ले का कार्ड", "tarjeta regalo",
    "card ke peeche ka number", "मुझे गिफ्ट कार्ड्स चाहिए।",
])
def test_gift_card_variants_refused(text):
    assert action(text) == "refuse"
    assert action(text + "।") == "refuse"


@pytest.mark.parametrize("text", [
    "my Apple Card", "pay with my Target card", "my Amazon card", "Visa card", "मेरे वीज़ा कार्ड से, मेरा ओटीपी आ गया",
    "I'll pay with my Visa card. Can you read me the numbers on the soup label?",
])
def test_credit_cards_and_own_otp_proceed(text):
    assert action(text) == "proceed"


@pytest.mark.parametrize("text", ["an Amazon gift card", "a prepaid Target card", "tarjetas de regalo de Amazon"])
def test_brand_with_gift_or_prepaid_context_refused(text):
    assert action(text) == "refuse"


# ---------------------------------------------------------------- terms as said

@pytest.mark.parametrize("text,term", [
    ("Compra tarjetas de Google Play ahora", "tarjetas de Google Play"),
    ("Necesito la fianza", "fianza"),
    ("मुझे गिफ्ट कार्ड्स चाहिए।", "गिफ्ट कार्ड्स"),
    ("Don’t tell Priyank", "Don’t tell"),
])
def test_hit_terms_are_the_original_words(text, term):
    assert term in [h["term"] for h in screen(text, session_id="s_terms")["hits"]]


# ---------------------------------------------------------------- session memory and history

def test_session_memory_is_bounded(monkeypatch):
    from policy import screen as screen_mod
    monkeypatch.setattr(screen_mod, "MAX_SESSIONS", 3)
    for i in range(5):
        screen("gift card", session_id=f"s_bound_{i}")
    assert len(screen_mod._refused) == 3
    assert screen("bread", session_id="s_bound_0")["action"] == "proceed"
    assert screen("bread", session_id="s_bound_4")["action"] == "judge"


def test_reset_sessions_clears_memory():
    screen("gift card", session_id="s_reset")
    reset_sessions()
    assert screen("bread", session_id="s_reset")["action"] == "proceed"


def test_history_summary_names_the_refused_item():
    from policy.screen import history_summary
    assert history_summary("s_hist") == ""
    screen("मुझे गिफ्ट कार्ड चाहिए", session_id="s_hist")
    assert "गिफ्ट कार्ड" in history_summary("s_hist")
    screen("read him the code on the back of the card", session_id="s_hist2")
    assert "card numbers or codes" in history_summary("s_hist2")


class _FakeCompletions:
    def __init__(self, content, delay=0.0):
        self.content, self.delay, self.calls = content, delay, []

    def create(self, **kwargs):
        import time
        from types import SimpleNamespace as NS
        self.calls.append(kwargs)
        time.sleep(self.delay)
        return NS(choices=[NS(message=NS(content=self.content))],
                  usage=NS(prompt_tokens_details=NS(cached_tokens=0)))


@pytest.fixture
def fake_client(monkeypatch):
    from types import SimpleNamespace as NS

    def install(content='{"scam_score": 0.1, "patterns": ["none"], "rationale": "ok", "action": "proceed"}', delay=0.0):
        completions = _FakeCompletions(content, delay)
        monkeypatch.setenv("JUDGE_FAKE", "0")
        monkeypatch.setattr(judge_mod, "_client", lambda: NS(chat=NS(completions=completions)))
        return completions
    return install


def test_judge_gets_session_history(fake_client):
    completions = fake_client()
    screen("gift card", session_id="s_hist3")
    judge_mod.judge("medicine and bread", {"items": [{"sku": "RX-001", "qty": 1}]}, session_id="s_hist3")
    body = json.loads(completions.calls[0]["messages"][1]["content"])
    assert "gift card" in body["history_summary"]
    assert body["cart"] == [{"sku": "RX-001", "qty": 1}]


def test_judge_drops_unknown_patterns(fake_client):
    fake_client('{"scam_score": 0.9, "patterns": ["urgency", "made_up"], "rationale": "x", "action": "refuse_and_alert"}')
    out = judge_mod.judge("hurry")
    assert out["patterns"] == ["urgency"]
    jsonschema.validate(out, JUDGE_CONTRACT)


def test_judge_hard_deadline(fake_client, monkeypatch):
    import time
    fake_client(delay=2.0)
    monkeypatch.setenv("JUDGE_TIMEOUT_S", "0.3")
    start = time.perf_counter()
    with pytest.raises(judge_mod.JudgeError):
        judge_mod.judge("hello")
    assert time.perf_counter() - start < 1.0


def test_judge_route_accepts_cart_dict():
    body = TestClient(app).post("/judge", json={"transcript": "bread", "cart": {"items": []}}).json()
    jsonschema.validate(body, JUDGE_CONTRACT)


# ---------------------------------------------------------------- spoken lines

LINE_KEYS = {"ordering_now", "asking_priya", "caregiver_approved", "caregiver_declined", "caregiver_timeout",
             "receipt_done", "checkout_unavailable", "over_monthly_cap", "read_back_required",
             "blocked_category", "scam_pattern", "code_reading"}


def test_line_files_share_keys_and_placeholders():
    import re
    files = {lang: json.loads((ROOT / f"ai/prompts/lines.{lang}.json").read_text(encoding="utf-8")) for lang in ("en", "es", "hi")}
    assert LINE_KEYS <= set(files["en"])
    assert set(files["en"]) == set(files["es"]) == set(files["hi"])
    for key in files["en"]:
        holes = {lang: set(re.findall(r"\{(\w+)\}", d[key])) for lang, d in files.items()}
        assert holes["en"] == holes["es"] == holes["hi"], key


@pytest.mark.parametrize("name", [f"line.{k}.{l}.mp3" for k in ("asking_priya", "receipt_done") for l in ("en", "es", "hi")])
def test_line_clips_exist(name):
    assert (ROOT / "ai/warnings" / name).exists()


# ---------------------------------------------------------------- brand cards, amounts and platforms

@pytest.mark.parametrize("text", [
    "Buy five hundred dollars of Apple cards for my grandson, it's urgent",
    "buy me two Amazon cards for my grandson",
    "three hundred dollars worth of Target cards",
    "Compra quinientos dólares en tarjetas de Apple para mi nieto",
    "पांच सौ डॉलर के एप्पल कार्ड",
    "paanch sau dollar ke Apple card",
])
def test_brand_cards_and_amounts_of_cards_refused(text):
    assert action(text) == "refuse"


@pytest.mark.parametrize("text", ["my Apple Card", "I'll pay with my Amazon card", "my Target card has a discount"])
def test_ones_own_brand_card_proceeds(text):
    assert action(text) == "proceed"


@pytest.mark.parametrize("text", ["कार्ड का ओटीपी दे दो", "card ka OTP de do", "कार्ड का कोड बोल दीजिए", "card ka pin bata dena"])
def test_hindi_otp_verbs_with_a_card_refused(text):
    assert action(text) == "refuse"


@pytest.mark.parametrize("text", ["ओटीपी दीजिए", "OTP bol do", "कोड शेयर कर दो", "पिन बताएं"])
def test_hindi_otp_verbs_alone_are_code_reading(text):
    assert evaluate(text).rules == ["R_code_reading"]


# Adversarial probe, each decided: all five are money-like instruments, so all refuse.
@pytest.mark.parametrize("text", [
    "g i f t card", "googleplay card", "Google Play recharge kar do", "buy iTunes for my grandson", "steam wallet code",
])
def test_probe_variants_refused(text):
    assert action(text) == "refuse"


def test_spelled_letters_join_only_in_runs_of_three():
    assert normalize("g i f t card") == "gift card"
    assert normalize("I need a b") == "i nid a b"


def test_grandchild_with_urgency_goes_to_the_judge():
    assert action("my grandson needs it, it's urgent") == "judge"
    assert action("मेरे पोते को अभी चाहिए, जल्दी") == "judge"
