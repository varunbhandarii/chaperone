"""Catalog + profile service (port 8003).

    python -m uvicorn catalog.search:app --host 0.0.0.0 --port 8003

GET /search?q=bread          ranked items; the shopper's usual brand is flagged
GET /resolve?q=my blood pressure medicine and bread
                             profile phrases -> saved items (pharmacy pickup, usual bread)
GET /suggest?sku=BAK-001     discovery: alternatives in the same group with spoken-friendly reasons
GET /items/{sku}             one item (the merchant prices carts from this, never from the agent)
"""

import json
import re
import unicodedata
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

HERE = Path(__file__).parent
CATALOG_PATH = HERE / "catalog.json"
PROFILE_PATH = HERE / "profile.json"

# Never upsell these: prescriptions are fixed, and gift/prepaid cards are what scammers ask for.
GIFT_LIKE_CATEGORIES = {"gift_card", "prepaid_card"}
NO_SUGGEST_CATEGORIES = {"pharmacy_pickup"} | GIFT_LIKE_CATEGORIES

STOPWORDS = {
    # en
    "a", "an", "the", "my", "some", "of", "and", "please", "i", "want", "need", "buy", "get", "me", "to", "for", "usual",
    # es
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "mi", "mis", "y", "quiero", "necesito",
    "comprar", "por", "favor", "para",
    # hi (Latin)
    "mera", "meri", "mere", "ki", "ka", "ke", "aur", "chahiye", "lao", "do",
}


def normalize(text: str) -> str:
    # Strip Latin accents only (U+0300-036F); Devanagari vowel signs are also combining marks and must stay.
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not 0x300 <= ord(ch) <= 0x36F)
    # Punctuation/symbols -> space. Not [^\w], because \w drops Devanagari vowel signs (Mn/Mc).
    text = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def stem(token: str) -> str:
    for suffix in ("es", "s"):
        if len(token) > 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def tokens(text: str) -> list[str]:
    return [stem(t) for t in normalize(text).split() if t not in STOPWORDS]


def contains_phrase(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", haystack) is not None


class Catalog:
    def __init__(self, catalog: dict, profile: dict):
        self.items = {it["sku"]: it for it in catalog["items"]}
        self.group_aliases = {g: [normalize(a) for a in aliases] for g, aliases in catalog["group_aliases"].items()}
        self.profile = profile
        self.usual_skus = {u["sku"] for u in profile.get("usuals", [])}
        self.prefer_tags = set(profile.get("preferences", {}).get("prefer_tags", []))
        # (own words: name/brand/tags, group words: category + shopper aliases shared by the whole group)
        self._index = {sku: self._index_tokens(it) for sku, it in self.items.items()}

    @classmethod
    def load(cls, catalog_path: Path = CATALOG_PATH, profile_path: Path = PROFILE_PATH) -> "Catalog":
        return cls(json.loads(catalog_path.read_text()), json.loads(profile_path.read_text()))

    def _index_tokens(self, it: dict) -> tuple[set[str], set[str]]:
        own = [it["name"], it["brand"]] + [t.replace("_", " ") for t in it["tags"]]
        group = [it["category"].replace("_", " "), it["group"].replace("_", " ")] + self.group_aliases.get(it["group"], [])
        return {t for w in own for t in tokens(w)}, {t for w in group for t in tokens(w)}

    def view(self, it: dict) -> dict:
        return {**it, "usual": it["sku"] in self.usual_skus}

    def search(self, q: str, limit: int = 10) -> list[dict]:
        qn = normalize(q)
        qtokens = tokens(q)
        scored = []
        for sku, it in self.items.items():
            own, group = self._index[sku]
            if it["category"] in GIFT_LIKE_CATEGORIES:
                own = set()  # "apples" must not surface the Apple Gift Card; only gift-card words find these
            score = 0.0
            for t in qtokens:
                if t in own:
                    score += 3
                elif t in group:
                    score += 2
                if len(t) >= 3 and any(w != t and (w.startswith(t) or (len(w) >= 5 and t.startswith(w))) for w in own):
                    score += 1  # "ibuprofeno" ~ "ibuprofen", "chick" ~ "chicken"
            if any(contains_phrase(qn, a) for a in self.group_aliases.get(it["group"], []) if " " in a):
                score += 5  # multi-word alias like "gift card" or "dard ki dawai"
            if score > 0:
                scored.append((score, sku in self.usual_skus, -it["price"], it))
        scored.sort(key=lambda s: s[:3], reverse=True)
        return [self.view(s[3]) for s in scored[:limit]]

    def resolve(self, q: str) -> list[dict]:
        """Profile phrases found in the utterance, longest phrase first, one hit per saved item."""
        qn = normalize(q)
        hits = []
        for usual in self.profile.get("usuals", []):
            phrases = sorted((normalize(p) for p in usual["phrases"]), key=len, reverse=True)
            match = next((p for p in phrases if contains_phrase(qn, p)), None)
            if match and usual["sku"] in self.items:
                hits.append({
                    "sku": usual["sku"],
                    "label": usual["label"],
                    "matched": match,
                    "note": usual.get("note", ""),
                    "confidence": 0.95,
                    "item": self.view(self.items[usual["sku"]]),
                })
        return hits

    def suggest(self, sku: str, budget: float | None = None, limit: int = 3) -> list[dict]:
        """Alternatives in the same group, with reasons the voice agent can read out."""
        base = self.items[sku]
        if base["category"] in NO_SUGGEST_CATEGORIES:
            return []
        out = []
        for it in self.items.values():
            if it["sku"] == sku or it["group"] != base["group"]:
                continue
            if it.get("substitute_key") != base.get("substitute_key"):
                continue  # medicines only swap for the same active ingredient
            if budget is not None and it["price"] > budget:
                continue
            reasons = []
            gained = sorted((set(it["tags"]) - set(base["tags"])) & self.prefer_tags)
            if gained:
                reasons.append(", ".join(t.replace("_", " ") for t in gained))
            diff = round(base["price"] - it["price"], 2)
            if diff > 0:
                reasons.append(f"${diff:.2f} cheaper")
            if it["sku"] in self.usual_skus:
                reasons.append("your usual")
            if not reasons:
                continue
            out.append({**self.view(it), "reasons": reasons, "_rank": (len(gained), diff)})
        out.sort(key=lambda s: s["_rank"], reverse=True)
        for s in out:
            del s["_rank"]
        return out[:limit]


catalog = Catalog.load()
app = FastAPI(title="Chaperone catalog")


@app.get("/health")
def health():
    return {"ok": True, "items": len(catalog.items)}


@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50)):
    return {"q": q, "items": catalog.search(q, limit)}


@app.get("/resolve")
def resolve(q: str = Query(..., min_length=1)):
    return {"q": q, "matches": catalog.resolve(q)}


@app.get("/suggest")
def suggest(sku: str, budget: float | None = None, limit: int = Query(3, ge=1, le=10)):
    if sku not in catalog.items:
        raise HTTPException(404, f"unknown sku {sku}")
    return {"sku": sku, "alternatives": catalog.suggest(sku, budget, limit)}


@app.get("/items/{sku}")
def get_item(sku: str):
    if sku not in catalog.items:
        raise HTTPException(404, f"unknown sku {sku}")
    return catalog.view(catalog.items[sku])


@app.get("/profile")
def profile():
    return catalog.profile
