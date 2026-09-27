"""Live Kroger search for what the snapshot doesn't carry.

catalog.json stays first: Ruth's usual products and the demo's items never change under her. When a search finds
nothing there that matches every word asked for ("denture adhesive", "cat food"), the catalog service asks
Kroger's Products API at the same Atlanta store the snapshot came from and maps each product the way
build_catalog does: the store from its category, the category Priyank's rules talk about, the promo price when there
is one. Gift cards stay a blocked category and alcohol or tobacco one his rules don't allow, so the rules still
decide. Items are kept in a shared file, so policy and the merchant price a live item exactly as search showed it.

    KROGER_CLIENT_ID / KROGER_CLIENT_SECRET   developer.kroger.com, scope product.compact (no live search without)
    KROGER_LIVE=0                             snapshot only
    KROGER_LOCATION_ID                        the store; default the nearest to KROGER_ZIP (30308)
    CATALOG_LIVE_PATH                         the shared file (default sessions/catalog_live.json; "" = none)

Kroger allows 10,000 product searches a day. A search's answer is kept TERM_TTL_S; an item ITEM_TTL_S, so a cart
built in the morning still prices in the afternoon. A search gets BUDGET_S; a later answer is still kept for the
next time someone asks.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import httpx

from catalog import build_catalog, seed_kroger

ROOT = Path(__file__).resolve().parents[1]
TERM_TTL_S = 6 * 3600
ITEM_TTL_S = 48 * 3600
BUDGET_S = 2.0
PER_SEARCH = 20

# Kroger's categories -> the store category build_catalog maps to a store and to the rules' category.
FOOD = {"Dairy": "dairy", "Produce": "produce", "Bakery": "bakery", "Beverages": "beverages", "Frozen": "frozen",
        "Meat & Seafood": "meat", "Deli": "deli", "Pet Care": "pet"}
HEALTH = {"Health", "Pharmacy"}
CARE = {"Personal Care", "Beauty", "Baby"}
HOME = {"Cleaning Products", "Kitchen", "Home", "Home & Garden", "Garden", "Party", "Holiday & Seasonal Goods",
        "Floral", "Apparel", "Electronics", "Toys", "Office", "Automotive", "Hardware", "Paper Products", "Laundry"}
RESTRICTED = {"Adult Beverage", "Tobacco"}


def enabled() -> bool:
    return (os.environ.get("KROGER_LIVE", "1") != "0" and bool(os.environ.get("KROGER_CLIENT_ID"))
            and bool(os.environ.get("KROGER_CLIENT_SECRET")))


def store_path() -> Path | None:
    raw = os.environ.get("CATALOG_LIVE_PATH")
    if raw == "":
        return None
    return Path(raw) if raw else ROOT / "sessions" / "catalog_live.json"


def term_key(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'&-]", " ", q.lower())).strip()


def category_for(product: dict) -> str:
    cats = set(product.get("categories") or [])
    if "Gift Cards" in cats:
        return "gift_card"
    if cats & RESTRICTED or product.get("alcohol") is True or product.get("ageRestriction") is True:
        return "age_restricted"
    for cat in product.get("categories") or []:
        if cat in HEALTH:
            return "otc_medicine"
        if cat in CARE:
            return "personal_care"
        if cat in HOME:
            return "household"
        if cat in FOOD:
            return FOOD[cat]
    return "pantry"


def group_for(term: str, product: dict) -> str:
    """A known search group when the words are one ("toilet paper"); else Kroger's own category ("pet_care").
    Never the words themselves: Kroger's search is loose, and an item must match by its own name to rank."""
    key = term_key(term)
    for group, aliases in build_catalog.GROUP_ALIASES.items():
        if key in (a.lower() for a in aliases):
            return group
    first = next(iter(product.get("categories") or []), "other")
    return re.sub(r"[^a-z0-9]+", "_", first.lower()).strip("_") or "other"


def to_item(product: dict, term: str) -> dict | None:
    category = category_for(product)
    item = seed_kroger.to_item(product, group_for(term, product), category)
    if item is None:
        return None  # not sold at this store today
    return {**item, "merchant": build_catalog.STORE_BY_CATEGORY.get(category, build_catalog.MERCHANT),
            "mandate_category": build_catalog.MANDATE_CATEGORY[category], "product_key": item["sku"],
            "source": "kroger_live", "fetched_at": time.time()}


class LiveStore:
    """The shared file of live items: the catalog service writes it; policy and the merchant read it to price."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self._stamp: tuple | None = None
        self.data: dict = {"location_id": None, "items": {}, "terms": {}}

    def load(self) -> dict:
        if not self.path:
            return self.data
        try:
            stat = self.path.stat()
        except OSError:
            return self.data
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self._stamp:
            try:
                self.data = {"location_id": None, "items": {}, "terms": {}, **json.loads(self.path.read_text(encoding="utf-8"))}
                self._stamp = stamp
            except (OSError, ValueError):
                pass  # mid-write or damaged: keep what was read before
        return self.data

    def item(self, sku: str) -> dict | None:
        found = self.load()["items"].get(sku)
        if found and time.time() - float(found.get("fetched_at") or 0) < ITEM_TTL_S:
            return found
        return None

    def save(self, update) -> None:
        if not self.path:
            update(self.data)
            return
        with self._lock:
            data = self.load()
            update(data)
            now = time.time()
            data["items"] = {k: v for k, v in data["items"].items() if now - float(v.get("fetched_at") or 0) < ITEM_TTL_S}
            data["terms"] = {k: v for k, v in data["terms"].items() if now - float(v.get("at") or 0) < TERM_TTL_S}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            for _ in range(5):
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:  # a reader has it open on Windows; try again in a moment
                    time.sleep(0.05)
            self.data = data


class LiveKroger:
    def __init__(self, store: LiveStore, http=httpx.Client):
        self.store = store
        self._http = http
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kroger")
        self._token: tuple[str, float] | None = None
        self._token_lock = threading.Lock()

    def _client(self) -> httpx.Client:
        return self._http(timeout=5.0, headers={"Authorization": f"Bearer {self._bearer()}", "Accept": "application/json"})

    def _bearer(self) -> str:
        with self._token_lock:
            if self._token and time.time() < self._token[1]:
                return self._token[0]
            basic = base64.b64encode(f"{os.environ['KROGER_CLIENT_ID']}:{os.environ['KROGER_CLIENT_SECRET']}".encode()).decode()
            with self._http(timeout=5.0) as client:
                r = client.post(f"{seed_kroger.API}/connect/oauth2/token", headers={"Authorization": f"Basic {basic}"},
                                data={"grant_type": "client_credentials", "scope": "product.compact"})
            r.raise_for_status()
            body = r.json()
            self._token = (body["access_token"], time.time() + int(body.get("expires_in") or 1800) - 60)
            return self._token[0]

    def location_id(self) -> str:
        known = os.environ.get("KROGER_LOCATION_ID") or self.store.load().get("location_id")
        if known:
            return known
        with self._client() as client:
            r = client.get(f"{seed_kroger.API}/locations",
                           params={"filter.zipCode.near": os.environ.get("KROGER_ZIP", "30308"), "filter.limit": 1})
        r.raise_for_status()
        found = r.json()["data"][0]["locationId"]
        self.store.save(lambda data: data.update(location_id=found))
        return found

    def warm(self) -> None:
        """Sign in and find the store before the first search, so that one pays only for the search."""
        def run():
            try:
                self.location_id()
            except Exception as e:  # noqa: BLE001 - live search stays best effort
                print(f"[kroger] warm-up: {type(e).__name__}: {e}", flush=True)
        self._pool.submit(run)

    def _fetch(self, term: str) -> list[dict]:
        location = self.location_id()
        with self._client() as client:
            r = client.get(f"{seed_kroger.API}/products", params={"filter.term": term[:100], "filter.locationId": location,
                                                                  "filter.limit": PER_SEARCH})
        r.raise_for_status()
        items = [it for it in (to_item(p, term) for p in r.json().get("data") or []) if it]

        def keep(data: dict) -> None:
            data["items"].update({it["sku"]: it for it in items})
            data["terms"][term_key(term)] = {"at": time.time(), "skus": [it["sku"] for it in items]}

        self.store.save(keep)
        return items

    def cached(self, term: str) -> list[dict] | None:
        data = self.store.load()
        hit = data["terms"].get(term_key(term))
        if not hit or time.time() - float(hit.get("at") or 0) >= TERM_TTL_S:
            return None
        return [data["items"][s] for s in hit.get("skus") or [] if s in data["items"]]

    def search(self, term: str, budget: float = BUDGET_S) -> list[dict]:
        """Kroger's products for the words, within the budget; [] when Kroger is slow, down or has nothing."""
        cached = self.cached(term)
        if cached is not None:
            return cached
        future = self._pool.submit(self._fetch, term)
        try:
            return future.result(timeout=budget)
        except FutureTimeout:
            print(f"[kroger] {term!r}: no answer within {budget:.1f} s; kept for next time", flush=True)
            return []
        except Exception as e:  # noqa: BLE001 - the snapshot still answers
            print(f"[kroger] {term!r}: {type(e).__name__}: {e}", flush=True)
            return []
