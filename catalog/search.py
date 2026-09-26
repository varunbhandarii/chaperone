"""Catalog + profile service (port 8003).

    python -m uvicorn catalog.search:app --host 0.0.0.0 --port 8003

GET /search?q=bread[&store=parkside_pharmacy]
                             ranked items across every store; each names its merchant and store, flags the
                             shopper's usual product and lists the same product elsewhere[]. Ordering: the
                             strongest matches first, and among those the usual first, then the lowest price.
                             One product sold at two stores is one result (the better offer), not two.
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

from common import merchants

HERE = Path(__file__).parent
CATALOG_PATH = HERE / "catalog.json"
PROFILE_PATH = HERE / "profile.json"

# Never upsell these: prescriptions are fixed, and gift/prepaid cards are what scammers ask for.
GIFT_LIKE_CATEGORIES = {"gift_card", "prepaid_card"}
NO_SUGGEST_CATEGORIES = {"pharmacy_pickup"} | GIFT_LIKE_CATEGORIES

# Tag words shoppers say in es/hi, so "sopa baja en sodio" finds low_sodium items.
TAG_ALIASES = {
    "low_sodium": ["bajo en sodio", "baja en sodio", "poca sal", "sin sal", "kam namak", "कम नमक"],
    "reduced_sugar": ["menos azucar", "sin azucar", "kam cheeni"],
    "lactose_free": ["sin lactosa"],
    "organic": ["organico", "organica"],
    "whole_grain": ["integral"],
}

STOPWORDS = {
    # en
    "a", "an", "the", "my", "some", "of", "and", "please", "i", "want", "need", "buy", "get", "me", "to", "for", "usual",
    # es
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "mi", "mis", "y", "quiero", "necesito",
    "comprar", "por", "favor", "para",
    # hi (Latin)
    "mera", "meri", "mere", "ki", "ka", "ke", "aur", "chahiye", "lao", "do",
}


# Words that name "some medicine" without saying which. They match every OTC item (category otc_medicine),
# so they only rank items when nothing in the query is more specific.
GENERIC_WORDS = [
    "medicine", "medicines", "medication", "medications", "meds", "otc", "pill", "pills", "tablet", "tablets",
    "medicina", "medicinas", "medicamento", "medicamentos", "pastilla", "pastillas",
    "dawai", "dawa", "dava", "goli", "दवाई", "दवा", "गोली",
]
GENERIC_WEIGHT = 0.5
PROFILE_GROUP_BONUS = 4
# Matches within this much of the best score count as equally relevant, so price (not a word or two more in a
# long Kroger title) decides among them. Wider would let a brand-only hit ("Peter Pan" for "pan") in.
STRONG_MARGIN = 1
SAME_BRAND_ON_TOP = 2  # then other brands get a turn: a list of ten store-brand items isn't a comparison


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


GENERIC = {t for w in GENERIC_WORDS for t in tokens(w)}


class Catalog:
    def __init__(self, catalog: dict, profile: dict):
        self.items = {it["sku"]: it for it in catalog["items"]}
        self.group_aliases = {g: [normalize(a) for a in aliases] for g, aliases in catalog["group_aliases"].items()}
        self.profile = profile
        self.usual_skus = {u["sku"] for u in profile.get("usuals", [])}
        # Her usual is a product, whichever store sells it.
        self.usual_products = {self.items[s].get("product_key", s) for s in self.usual_skus if s in self.items}
        self.copies: dict[str, list[str]] = {}
        for sku, it in self.items.items():
            self.copies.setdefault(it.get("product_key", sku), []).append(sku)
        self.prefer_tags = set(profile.get("preferences", {}).get("prefer_tags", []))
        # (own words: name/brand/tags, group words: category + shopper aliases shared by the whole group)
        self._index = {sku: self._index_tokens(it) for sku, it in self.items.items()}

    @classmethod
    def load(cls, catalog_path: Path = CATALOG_PATH, profile_path: Path = PROFILE_PATH) -> "Catalog":
        return cls(json.loads(catalog_path.read_text(encoding="utf-8")), json.loads(profile_path.read_text(encoding="utf-8")))

    def _index_tokens(self, it: dict) -> tuple[set[str], set[str]]:
        own = [it["name"], it["brand"]] + [t.replace("_", " ") for t in it["tags"]]
        own += [a for t in it["tags"] for a in TAG_ALIASES.get(t, [])]
        group = [it["category"].replace("_", " "), it["group"].replace("_", " ")] + self.group_aliases.get(it["group"], [])
        return {t for w in own for t in tokens(w)}, {t for w in group for t in tokens(w)}

    def is_usual(self, it: dict) -> bool:
        return it.get("product_key", it["sku"]) in self.usual_products

    def view(self, it: dict) -> dict:
        merchant = it.get("merchant", merchants.DEFAULT)
        elsewhere = [{"merchant": o["merchant"], "store": merchants.name(o["merchant"]), "sku": o["sku"],
                      "price": o["price"]}
                     for o in (self.items[s] for s in self.copies.get(it.get("product_key", it["sku"]), []))
                     if o["sku"] != it["sku"]]
        return {**it, "merchant": merchant, "store": merchants.name(merchant), "usual": self.is_usual(it),
                "elsewhere": sorted(elsewhere, key=lambda o: o["price"])}

    @staticmethod
    def store_id(store: str | None) -> str | None:
        """A merchant id or a store's display name ("Parkside Pharmacy"); None for no filter."""
        if not store:
            return None
        wanted = normalize(store).replace(" ", "_")
        for m in merchants.all_merchants():  # "parkside", "Parkside Pharmacy" and "parkside_pharmacy" all work
            if any(name.startswith(wanted) for name in (m["id"], normalize(m["name"]).replace(" ", "_"))):
                return m["id"]
        return wanted

    def search(self, q: str, limit: int = 10, store: str | None = None) -> list[dict]:
        qn = normalize(q)
        qtokens = tokens(q)
        specific = [t for t in qtokens if t not in GENERIC]
        generic = [t for t in qtokens if t in GENERIC]
        # A multi-word profile phrase ("blood pressure medicine", "la presion") says which group the shopper means.
        profile_groups = {
            self.items[u["sku"]]["group"] for u in self.profile.get("usuals", []) if u["sku"] in self.items
            and any(" " in normalize(p) and contains_phrase(qn, normalize(p)) for p in u["phrases"])
        }
        store_id = self.store_id(store)
        scored = []
        for sku, it in self.items.items():
            if store_id and it.get("merchant", merchants.DEFAULT) != store_id:
                continue
            own, group = self._index[sku]
            if it["category"] in GIFT_LIKE_CATEGORIES:
                own = set()  # "apples" must not surface the Apple Gift Card; only gift-card words find these
            score = 0.0
            for t in specific:
                if t in own:
                    score += 3
                if t in group:
                    score += 2  # additive: "pan" in a bread name beats the "Peter Pan" brand
                if len(t) >= 3 and any(w != t and (w.startswith(t) or (len(w) >= 5 and t.startswith(w))) for w in own):
                    score += 1  # "ibuprofeno" ~ "ibuprofen", "chick" ~ "chicken"
            if any(contains_phrase(qn, a) for a in self.group_aliases.get(it["group"], []) if " " in a):
                score += 5  # multi-word alias like "gift card" or "dard ki dawai"
            if it["group"] in profile_groups:
                score += PROFILE_GROUP_BONUS
            weak = GENERIC_WEIGHT * sum((t in own) + (t in group) for t in generic)
            if score > 0 or weak > 0:
                scored.append((score + weak, score, it))
        if specific or profile_groups:  # "my blood pressure medicine" or "cough medicine" must not list every OTC
            scored = [s for s in scored if s[1] > 0]  # item; only a bare "medicine" does
        if not scored:
            return []
        best = max(s[0] for s in scored)
        # Strong matches first; among them her usual, then the lowest price, then the closer match.
        scored.sort(key=lambda s: (s[0] < best - STRONG_MARGIN, not self.is_usual(s[2]), s[2]["price"], -s[0],
                                   "store_brand" in s[2]["tags"]))
        results, seen = [], set()
        for _, _, it in scored:  # one result per product: its best offer, with the other stores in elsewhere
            key = it.get("product_key", it["sku"])
            if key not in seen:
                seen.add(key)
                results.append(it)
        return [self.view(it) for it in self._mix_brands(results, best, scored)[:limit]]

    def _mix_brands(self, results: list[dict], best: float, scored) -> list[dict]:
        """At most SAME_BRAND_ON_TOP strong results per brand before every other strong brand has had a turn."""
        strong_skus = {it["sku"] for total, _, it in scored if total >= best - STRONG_MARGIN}
        head, held, count = [], [], {}
        for it in results:
            if it["sku"] not in strong_skus:
                break
            brand = it["brand"].lower()
            if count.get(brand, 0) < SAME_BRAND_ON_TOP or self.is_usual(it):
                count[brand] = count.get(brand, 0) + 1
                head.append(it)
            else:
                held.append(it)
        return head + held + results[len(head) + len(held):]

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
            if self.is_usual(it):
                reasons.append("your usual")
            if it.get("merchant") != base.get("merchant"):
                reasons.append(f"at {merchants.name(it.get('merchant'))}")
            if not reasons:
                continue
            # Prefer the closest product (chicken noodle -> chicken noodle, not cream of mushroom).
            similar = len(set(tokens(it["name"])) & set(tokens(base["name"])))
            out.append({**self.view(it), "reasons": reasons, "_rank": (len(gained), similar, diff)})
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
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50), store: str | None = None):
    return {"q": q, "store": catalog.store_id(store), "items": catalog.search(q, limit, store)}


@app.get("/stores")
def stores():
    """The storefronts the agent can buy from, with how many items each sells."""
    counts: dict[str, int] = {}
    for it in catalog.items.values():
        counts[it.get("merchant", merchants.DEFAULT)] = counts.get(it.get("merchant", merchants.DEFAULT), 0) + 1
    return {"stores": [{"merchant": m["id"], "store": m["name"], "kind": m["kind"], "items": counts.get(m["id"], 0)}
                       for m in merchants.storefronts()]}


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
