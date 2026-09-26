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


# ---------------------------------------------------------------- R1 normalization

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


# ---------------------------------------------------------------- R2 card false refusals

@pytest.mark.parametrize("text", [
    "I will pay with my Visa card", "a birthday card for my grandson", "pagar con mi tarjeta Visa",
    "मेरे वीज़ा कार्ड से", "I need to call the number on the back of my card",
])
def test_card_mentions_proceed(text):
    assert action(text) == "proceed"


def test_medicare_card_is_not_refused():
    assert action("my Medicare card came") in {"proceed", "slow"}


@pytest.mark.parametrize("text", ["buy a visa gift card", "a prepaid visa", "two Apple cards", "tarjetas de Google Play"])
def test_instrument_phrases_refused(text):
    assert action(text) == "refuse"


# ---------------------------------------------------------------- R3 lexicon gaps

@pytest.mark.parametrize("text", ["bhai OTP batao", "read me the code", "léame los números", "ओटीपी बता दो"])
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
    assert set(evaluate("beta jaldi karo, turant").rules) == {"R_urgency", "R_family_emergency"}


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
