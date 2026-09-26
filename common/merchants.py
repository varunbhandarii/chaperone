"""The merchant registry, contracts/merchants.json, shared by catalog, merchant and policy.

    get("parkside_pharmacy")  -> the entry, or None
    storefronts()             -> merchants the agent may buy from (kind store or biller), in registry order
    credentials(entry)        -> (merchant_id, key_id, secret, separate) for that merchant's Cybersource account;
                                 separate is False when its <prefix>* variables are missing and the main
                                 account (VISA_ACCEPTANCE_*) stands in, tagged by purchase_prefix
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "contracts" / "merchants.json"
MAIN_PREFIX = "VISA_ACCEPTANCE_"
DEFAULT = "corner_market"
BUYABLE_KINDS = ("store", "biller")


@lru_cache(maxsize=1)
def registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def all_merchants() -> list[dict]:
    return registry()["merchants"]


def get(merchant_id: str | None) -> dict | None:
    return next((m for m in all_merchants() if m["id"] == merchant_id), None)


def storefronts() -> list[dict]:
    return [m for m in all_merchants() if m["kind"] in BUYABLE_KINDS]


def name(merchant_id: str | None) -> str:
    entry = get(merchant_id)
    return entry["name"] if entry else (merchant_id or "")


def _creds(prefix: str) -> tuple[str, str, str]:
    return tuple(os.environ.get(prefix + v, "").strip() for v in ("MERCHANT_ID", "API_KEY_ID", "SECRET_KEY"))


def credentials(entry: dict) -> tuple[str, str, str, bool]:
    prefix = entry.get("cybs_env")
    if prefix and prefix != MAIN_PREFIX:
        own = _creds(prefix)
        if all(own):
            return (*own, True)
    return (*_creds(MAIN_PREFIX), prefix == MAIN_PREFIX)
