from pathlib import Path

import pytest
import yaml

from rules import evaluate, normalize

LINES = yaml.safe_load((Path(__file__).parent / "lines.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang,line", [(l, s) for l, ss in LINES["refuse"].items() for s in ss])
def test_scam_lines_refused_pre_model(lang, line):
    v = evaluate(line)
    assert v.action == "refuse", v
    assert "R1_blocked_category" in v.rules


@pytest.mark.parametrize("lang,line", [(l, s) for l, ss in LINES["benign"].items() for s in ss])
def test_benign_lines_proceed(lang, line):
    assert evaluate(line).action == "proceed", evaluate(line)


def test_gift_card_line_carries_family_emergency():
    v = evaluate(LINES["refuse"]["en"][0])
    assert {"blocked_category", "family_emergency", "urgency", "secrecy"} <= set(v.patterns)


@pytest.mark.parametrize("j", [j for j in LINES["judge"] if j["label"] == "scam"], ids=lambda j: j["id"])
def test_coached_scams_escalate_to_judge(j):
    assert evaluate(j["text"]).action in {"judge", "refuse"}


def test_normalize_strips_accents_and_curly_quotes():
    assert normalize("¡Cárcel! Don’t") == "carcel don't"


def test_rules_are_fast():
    assert evaluate(LINES["refuse"]["es"][0]).elapsed_ms < 20
