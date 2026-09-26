"""Scam rule layer: deterministic, runs before any model call.

The lexicon lives in ai/rules/rules_v1.yaml; its header documents matching and the decision.

    python -m policy.rules "Mera pota jail mein hai, abhi gift card kharido"
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "ai" / "rules" / "rules_v1.yaml"

# Devanagari stays inside the word-boundary classes: Python's \w does not count matras
# as word characters, so without the range a boundary would fall inside a word.
_DEVANAGARI = "\u0900-\u097F"
_LEFT = rf"(?<![\w{_DEVANAGARI}])"
_RIGHT = rf"(?![\w{_DEVANAGARI}])"

_INVISIBLE = dict.fromkeys(map(ord, "\u200B\u200C\u200D\u00AD\uFEFF"))
_APOSTROPHES = dict.fromkeys(map(ord, "'\u2019\u2018\u02BC`"))
# Latin combining accents and the Devanagari nukta; never every Mn (that deletes matras
# and merges पोता, पता and पिता).
_STRIP_MARKS = re.compile("[\u0300-\u036F\u093C]")
_FOLDS = [
    (re.compile(r"\bnahin\b"), "nahi"),
    (re.compile(r"ph"), "f"),
    (re.compile(r"a{2,}"), "a"),
    (re.compile(r"e{2,}"), "i"),
    (re.compile(r"o{2,}"), "u"),
    (re.compile(r"z"), "j"),
    (re.compile(r"w"), "v"),
]


def _is_space_like(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in "PSZ" or ch in "\u0964\u0965"


def normalize(text: str) -> str:
    """Canonical form both utterances and lexicon terms are matched in."""
    text = unicodedata.normalize("NFKD", text).translate(_INVISIBLE).translate(_APOSTROPHES)
    text = _STRIP_MARKS.sub("", text).casefold()
    text = "".join(" " if _is_space_like(ch) else ch for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    for rx, sub in _FOLDS:
        text = rx.sub(sub, text)
    return text


def _normalize_regex(pattern: str) -> str:
    """Accent-strip, casefold and Hinglish-fold a lexicon regex, leaving escapes intact."""
    pattern = _STRIP_MARKS.sub("", unicodedata.normalize("NFKD", pattern)).casefold()
    pattern = pattern.translate(_APOSTROPHES)
    parts = re.split(r"(\\.)", pattern)
    for i in range(0, len(parts), 2):
        for rx, sub in _FOLDS:
            parts[i] = rx.sub(sub, parts[i])
    return "".join(parts)


def _compile(term: str) -> re.Pattern:
    body = _normalize_regex(term[3:]) if term.startswith("re:") else re.escape(normalize(term))
    return re.compile(f"{_LEFT}(?:{body}){_RIGHT}")


@dataclass
class Hit:
    rule_id: str
    severity: str
    pattern: str
    lang: str
    term: str
    matched: str
    weak: bool = False


@dataclass
class Verdict:
    action: str  # refuse | judge | slow | proceed
    rules: list[str]
    patterns: list[str]
    hits: list[Hit] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class RuleSet:
    def __init__(self, path: Path = RULES_PATH):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.version = spec["version"]
        self.soft_hits_for_judge = spec["soft_hits_for_judge"]
        self.rules = spec["rules"]
        self._instruments = [_compile(t) for terms in spec["instrument_terms"].values() for t in terms]
        self._terms: list[tuple[str, dict, str, str, bool, re.Pattern]] = []
        for rule_id, rule in self.rules.items():
            for key, weak in (("terms", False), ("weak_terms", True)):
                for lang, terms in rule.get(key, {}).items():
                    for term in terms:
                        self._terms.append((rule_id, rule, lang, term, weak, _compile(term)))

    def evaluate(self, text: str) -> Verdict:
        start = time.perf_counter()
        norm = normalize(text)
        hits: list[Hit] = []
        for rule_id, rule, lang, term, weak, rx in self._terms:
            m = rx.search(norm)
            if m:
                hits.append(Hit(rule_id, rule["severity"], rule["pattern"], lang, term, m.group(0), weak))

        # Weak terms (beta, pota, hospital) count only beside another soft rule.
        strong_soft = {h.rule_id for h in hits if h.severity == "soft" and not h.weak}
        hits = [h for h in hits if not h.weak or strong_soft - {h.rule_id}]

        if any(self.rules[h.rule_id].get("hard_with_instrument") for h in hits):
            if any(rx.search(norm) for rx in self._instruments):
                for h in hits:
                    if self.rules[h.rule_id].get("hard_with_instrument"):
                        h.severity = "hard"

        soft = {h.rule_id for h in hits if h.severity == "soft"}
        if any(h.severity == "hard" for h in hits):
            action = "refuse"
        elif len(soft) >= self.soft_hits_for_judge:
            action = "judge"
        elif soft:
            action = "slow"
        else:
            action = "proceed"
        return Verdict(
            action,
            list(dict.fromkeys(h.rule_id for h in hits)),
            list(dict.fromkeys(h.pattern for h in hits)),
            hits,
            round((time.perf_counter() - start) * 1000, 3),
        )


@lru_cache(maxsize=1)
def default_rules() -> RuleSet:
    return RuleSet()


def evaluate(text: str) -> Verdict:
    return default_rules().evaluate(text)


if __name__ == "__main__":
    line = " ".join(sys.argv[1:]) or sys.stdin.read()
    print(json.dumps(evaluate(line).to_dict(), ensure_ascii=False, indent=2))
