import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai.eval.run import combined, metrics  # noqa: E402

SCRIPTS = [
    {"id": "s1", "label": "scam", "expected": "refuse"},
    {"id": "s2", "label": "scam", "expected": "refuse"},
    {"id": "b1", "label": "benign", "expected": "allow"},
]


def test_held_is_counted_apart_and_not_as_caught():
    m = metrics(SCRIPTS, {"s1": "refuse", "s2": "held", "b1": "allow"})
    assert (m["tp"], m["fn"], m["held_scam"], m["fr"]) == (1, 1, 1, 0)


def test_a_timeout_holds_only_when_the_rules_asked_for_the_judge():
    preds = combined({"s1": "allow", "s2": "allow"}, {"s1": "judge", "s2": "proceed"},
                     {"s1": [None, None, 0.9], "s2": [None, None, None]}, 0.6)
    assert preds == {"s1": "held", "s2": "allow"}
