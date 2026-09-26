"""Chaperone scam rule layer v0: deterministic, runs before any model call.

    python prototypes/screen/rules.py "Buy gift cards right now, my grandson is in jail"
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).resolve().parents[2] / "ai" / "rules" / "rules_v0.yaml"

_DEVANAGARI = "ऀ-ॿ"
_LEFT = rf"(?<![\w{_DEVANAGARI}'])"
_RIGHT = rf"(?![\w{_DEVANAGARI}])"
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'"})


def normalize(text: str) -> str:
    """NFKD, drop Latin accents and the Devanagari nukta, casefold, punctuation to spaces."""
    text = unicodedata.normalize("NFKD", text.translate(_APOSTROPHES))
    text = re.sub(r"[̀-़ͯ]", "", text).casefold()
    text = re.sub(rf"[^\w{_DEVANAGARI}' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Hit:
    rule: str
    severity: str
    pattern: str
    lang: str
    term: str
    matched: str


@dataclass
class Verdict:
    action: str  # refuse | judge | proceed
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
        self._compiled: list[tuple[str, dict, str, str, re.Pattern]] = []
        for rule_id, rule in self.rules.items():
            for lang, terms in rule["terms"].items():
                for term in terms:
                    if term.startswith("re:"):
                        body = normalize_regex(term[3:])
                    else:
                        body = re.escape(normalize(term))
                    self._compiled.append(
                        (rule_id, rule, lang, term, re.compile(f"{_LEFT}(?:{body}){_RIGHT}"))
                    )

    def evaluate(self, text: str) -> Verdict:
        start = time.perf_counter()
        norm = normalize(text)
        hits: list[Hit] = []
        for rule_id, rule, lang, term, rx in self._compiled:
            m = rx.search(norm)
            if m:
                hits.append(Hit(rule_id, rule["severity"], rule["pattern"], lang, term, m.group(0)))

        fired = list(dict.fromkeys(h.rule for h in hits))
        patterns = list(dict.fromkeys(h.pattern for h in hits))
        hard = any(h.severity == "hard" for h in hits)
        soft = {h.rule for h in hits if h.severity == "soft"}
        if hard:
            action = "refuse"
        elif len(soft) >= self.soft_hits_for_judge:
            action = "judge"
        else:
            action = "proceed"
        elapsed = (time.perf_counter() - start) * 1000
        return Verdict(action, fired, patterns, hits, round(elapsed, 3))


def normalize_regex(pattern: str) -> str:
    """Lowercase and strip accents from a regex written in the YAML, keeping its syntax."""
    return re.sub(r"[̀-़ͯ]", "", unicodedata.normalize("NFKD", pattern)).casefold()


_default: RuleSet | None = None


def evaluate(text: str) -> Verdict:
    global _default
    if _default is None:
        _default = RuleSet()
    return _default.evaluate(text)


if __name__ == "__main__":
    line = " ".join(sys.argv[1:]) or sys.stdin.read()
    print(json.dumps(evaluate(line).to_dict(), ensure_ascii=False, indent=2))
