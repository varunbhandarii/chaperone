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
_DEVANAGARI_LETTER = re.compile("[\u0900-\u0963\u0966-\u097F]")  # letters, not the danda

_INVISIBLE = dict.fromkeys(map(ord, "\u200B\u200C\u200D\u00AD\uFEFF"))
_APOSTROPHES = dict.fromkeys(map(ord, "'\u2019\u2018\u02BC`"))
_DELETE = {chr(c) for c in (*_INVISIBLE, *_APOSTROPHES)}
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


def _fold(token: str) -> str:
    for rx, sub in _FOLDS:
        token = rx.sub(sub, token)
    return token


def tokens(text: str) -> list[tuple[str, int, int]]:
    """Normalized tokens with the [start, end) span each one came from in `text`.

    Punctuation, symbols, spaces and the danda split tokens; zero-width characters and
    apostrophes are deleted in place. Each token is NFKD-decomposed, stripped of Latin
    accents and the nukta, casefolded and Hinglish-folded.
    """
    out: list[tuple[str, int, int]] = []
    parts: list[str] = []
    start = end = 0
    for i, ch in enumerate(text):
        if ch in _DELETE:
            continue
        if ch.isspace() or _is_space_like(ch):
            if parts:
                out.append((_fold("".join(parts)), start, end))
                parts = []
            continue
        if not parts:
            start = i
        parts.append(_STRIP_MARKS.sub("", unicodedata.normalize("NFKD", ch)).casefold())
        end = i + 1
    if parts:
        out.append((_fold("".join(parts)), start, end))
    return [t for t in out if t[0]]


def normalize(text: str) -> str:
    """Canonical form both utterances and lexicon terms are matched in."""
    return " ".join(t for t, _, _ in tokens(text))


def _normalize_regex(pattern: str) -> str:
    """Accent-strip, casefold and Hinglish-fold a lexicon regex, leaving escapes intact."""
    pattern = _STRIP_MARKS.sub("", unicodedata.normalize("NFKD", pattern)).translate(_APOSTROPHES)
    parts = re.split(r"(\\.)", pattern)
    for i in range(0, len(parts), 2):
        parts[i] = _fold(parts[i].casefold())
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
        self.weak_ignores = set(spec.get("weak_needs_other_than", []))
        self._instruments = [_compile(t) for terms in spec["instrument_terms"].values() for t in terms]
        self._terms: list[tuple[str, dict, str, str, bool, re.Pattern]] = []
        for rule_id, rule in self.rules.items():
            for key, weak in (("terms", False), ("weak_terms", True)):
                for lang, terms in rule.get(key, {}).items():
                    for term in terms:
                        self._terms.append((rule_id, rule, lang, term, weak, _compile(term)))

    def evaluate(self, text: str, lang: str | None = None) -> Verdict:
        start = time.perf_counter()
        toks = tokens(text)
        norm = " ".join(t for t, _, _ in toks)
        # Where each token starts in `norm`, to map a match back to the words that were said.
        offsets, pos = [], 0
        for t, _, _ in toks:
            offsets.append(pos)
            pos += len(t) + 1

        def original(m: re.Match) -> str:
            inside = [i for i, off in enumerate(offsets) if off < m.end() and off + len(toks[i][0]) > m.start()]
            return text[toks[inside[0]][1]:toks[inside[-1]][2]] if inside else m.group(0)

        hits: list[Hit] = []
        for rule_id, rule, term_lang, term, weak, rx in self._terms:
            m = rx.search(norm)
            if m:
                hits.append(Hit(rule_id, rule["severity"], rule["pattern"], term_lang, term, original(m), weak))

        # Weak terms (son, beta, pota, hospital, medicare) count only in the utterance's own
        # language and only beside another soft rule that is not in weak_needs_other_than.
        spoken = guess_lang(text, lang)
        strong_soft = {h.rule_id for h in hits if h.severity == "soft" and not h.weak} - self.weak_ignores
        hits = [h for h in hits if not h.weak or (
            (spoken is None or _base_lang(h.lang) == spoken) and strong_soft - {h.rule_id})]

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


_FUNCTION_WORDS = {
    lang: {normalize(w) for w in words.split()}
    for lang, words in {
        "en": "the and my is i to of for with need buy want please me it this he she they you",
        "es": "el la los las de que y una un mi por para con necesito compra quiero esta me lo le es",
        "hi": "hai hain mein ko ka ki ke aur mujhe mera meri kya nahi se par pe karo kharido chahiye",
    }.items()
}
_SPANISH_MARKS = re.compile(r"[ñ¿¡áéíóú]", re.IGNORECASE)
LANGS = ("en", "es", "hi")


def _base_lang(lang: str) -> str:
    return "hi" if lang == "hi_latn" else lang


def guess_lang(text: str, requested: str | None = None) -> str | None:
    """en, es or hi from the request, Devanagari letters or function words; None if unclear."""
    if requested:
        base = requested.split("-")[0].lower()
        if base in LANGS:
            return base
    if _DEVANAGARI_LETTER.search(text):
        return "hi"
    words = normalize(text).split()
    votes = {lang: sum(w in fw for w in words) for lang, fw in _FUNCTION_WORDS.items()}
    best = max(votes, key=votes.get)
    if votes[best] and list(votes.values()).count(votes[best]) == 1:
        return best
    if _SPANISH_MARKS.search(text):
        return "es"
    return None


@lru_cache(maxsize=1)
def default_rules() -> RuleSet:
    return RuleSet()


def evaluate(text: str, lang: str | None = None) -> Verdict:
    return default_rules().evaluate(text, lang)


if __name__ == "__main__":
    line = " ".join(sys.argv[1:]) or sys.stdin.read()
    print(json.dumps(evaluate(line).to_dict(), ensure_ascii=False, indent=2))
