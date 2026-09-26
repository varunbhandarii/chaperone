import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from ai import explain as ex  # noqa: E402

DECISION = {
    "decision": "deny",
    "say_key": "refund_scam",
    "rules": [{"id": "R0_mandate_valid", "passed": True, "detail": "ok"},
              {"id": "S_screen_R_overpay_sendback", "passed": False, "detail": "refund_scam"}],
    "judge": None,
    "cart": {"items": [{"sku": "X", "name": "Bread", "qty": 1, "price": 3.49, "category": "grocery"}], "total": 3.49},
    "mandate_id": "m_ruth_2026_09",
    "session_id": "s1",
}


def test_fake_answer_has_every_field(monkeypatch):
    monkeypatch.setenv("EXPLAIN_FAKE", "1")
    out = ex.explain(DECISION)
    assert set(out) == set(ex.FIELDS) and not ex.problems(out)


def test_input_keeps_only_what_the_explanation_needs():
    got = ex.build_input(DECISION, ruth_said="x" * 500, screen_hits=[{"rule_id": "R", "pattern": "refund_overpay", "term": "too much", "lang": "en"}])
    assert got["rules_failed"] == [{"id": "S_screen_R_overpay_sendback", "detail": "refund_scam"}]
    assert got["screen_hits"] == [{"pattern": "refund_overpay", "words": "too much"}]
    assert got["ruth_heard"].startswith("Ruth, a real store never asks")
    assert len(got["ruth_said"]) == 400
    assert "mandate_id" not in json.dumps(got) and "category" not in got["cart"]["items"][0]


@pytest.mark.parametrize("field,text,problem", [
    ("headline", "R1_blocked_category fired", "id"),
    ("what_happened", "The judge scored 0.93.", "id"),
    ("rule_in_plain_words", "This was urgent!", "alarming"),
    ("rule_in_plain_words", "Authority impersonation was found.", "jargon"),
    ("what_you_can_do", "Call Ruth. Then call the bank.", "more than one sentence"),
])
def test_problems_catch_bad_tone(field, text, problem):
    out = dict(ex.FAKE, **{field: text})
    assert any(problem in p for p in ex.problems(out))


def test_missing_key_raises(monkeypatch):
    monkeypatch.setenv("EXPLAIN_FAKE", "0")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    ex._client.cache_clear()
    with pytest.raises(ex.ExplainError):
        ex.explain(DECISION)
    ex._client.cache_clear()
