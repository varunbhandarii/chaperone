"""Mandate canonicalization. The passkey assertion is stored beside the hashed bytes."""

from __future__ import annotations

import hashlib

import jcs

DEFAULT_MANDATE = {
    "mandate_id": "m_ruth_2026_09",
    "shopper": "ruth",
    "caregiver": "priyank",
    "currency": "USD",
    "per_purchase_cap": 60.00,
    "monthly_cap": 300.00,
    "approval_threshold": 40.00,
    "allowed_merchants": ["corner_market", "parkside_pharmacy", "main_street_home", "peachtree_power"],
    "allowed_categories": ["grocery", "pharmacy", "household", "utility_bill"],
    "blocked_categories": ["gift_card", "prepaid_card", "wire", "crypto", "lottery"],
    "languages": ["es", "en", "hi"],
    "valid_from": "2026-09-01",
    "valid_to": "2026-12-31",
    "billers": [{"merchant_id": "peachtree_power", "account_ref": "PP-2231-0098", "monthly_cap": 200}],
    "card": {
        "blocked_mccs": ["4829", "6051", "6540", "7995"],
        "category_caps": {"5411": 150, "5912": 80, "5310": 100, "5311": 100, "5251": 100},
        "default_cap": 60,
        "atm_daily_cap": 100,
        "unusual_multiplier": 3,
        "cooldown": {"hours": 24, "caps": {"5912": 25, "5310": 25, "5311": 25, "6011": 0, "default": 25}},
    },
    "trusted_contacts": [
        {"name": "Priyank", "relation": "son", "phone": "+1-404-555-0142"},
        {"name": "Alex", "relation": "grandson", "phone": "+1-404-555-0187"},
    ],
}

# Visa category labels keyed by MCC. Store names come from contracts/merchants.json.
_VISA_CATEGORY = {
    "5411": ("Grocery Stores, Supermarkets", "Groceries for Ruth, per purchase"),
    "5912": ("Drug Stores and Pharmacies", "Medicine for Ruth, per purchase"),
    "5251": ("Hardware Stores", "Household items for Ruth, per purchase"),
    "4900": ("Utilities", "Ruth's power bill"),
}


def visa_stores() -> dict:
    from common.merchants import storefronts

    rows = {}
    for entry in storefronts():
        category, description = _VISA_CATEGORY.get(str(entry.get("mcc")), (entry["name"], entry["name"]))
        rows[entry["id"]] = (entry["name"], category, str(entry.get("mcc")), description)
    return rows

MONTHLY_BASELINE = 142.10


def fill_v2(mandate: dict, widen: bool = True) -> dict:
    """Show the new stores, categories and billers even when a v1 file was signed earlier.

    The signed file is not rewritten. A later passkey sign stores the filled fields. With widen=False (what
    checkout enforces) the stores, categories and billers stay exactly as signed; only missing restrictions
    (card rules, trusted contacts) take their defaults.
    """
    filled = dict(mandate)
    if not widen:
        for key in ("card", "trusted_contacts"):
            if not filled.get(key):
                filled[key] = DEFAULT_MANDATE[key]
        return filled
    for key in ("allowed_merchants", "allowed_categories"):
        have = list(filled.get(key) or [])
        for item in DEFAULT_MANDATE[key]:
            if item not in have:
                have.append(item)
        filled[key] = have
    for key in ("billers", "card", "trusted_contacts"):
        if not filled.get(key):
            filled[key] = DEFAULT_MANDATE[key]
    return filled


def canonical_mandate(mandate: dict) -> dict:
    return {key: value for key, value in mandate.items() if key != "passkey"}


def mandate_hash(mandate: dict) -> bytes:
    return hashlib.sha256(jcs.canonicalize(canonical_mandate(mandate))).digest()


def visa_view(mandate: dict) -> dict:
    """The same rules in Visa Intelligent Commerce's mandates[] shape. One entry per store or biller."""
    from datetime import datetime, timezone

    card = mandate.get("card") or {}
    caps = card.get("category_caps") or {}
    per_purchase = f"{float(mandate.get('per_purchase_cap') or 0):.2f}"
    try:
        until = datetime.fromisoformat(str(mandate.get("valid_to")) + "T23:59:59+00:00")
        epoch = str(int(until.timestamp()))
    except ValueError:
        epoch = "1798761599"
    entries = []
    stores = visa_stores()
    for merchant_id in mandate.get("allowed_merchants") or []:
        if merchant_id not in stores:
            continue
        name, category, mcc, description = stores[merchant_id]
        if merchant_id == "peachtree_power":
            bill = next((row for row in (mandate.get("billers") or []) if row.get("merchant_id") == merchant_id), {})
            amount = f"{float(bill.get('monthly_cap') or 0):.2f}"
        else:
            amount = per_purchase  # what the agent may spend per purchase there (card swipe caps are separate)
        mandate_id = f"{mandate.get('mandate_id')}-{merchant_id}"[:50]
        entries.append({
            "mandateId": mandate_id,
            "preferredMerchantName": name,
            "merchantCategory": category,
            "merchantCategoryCode": mcc,
            "declineThreshold": {"amount": amount or per_purchase, "currencyCode": mandate.get("currency") or "USD"},
            "effectiveUntilTime": epoch,
            "description": description,
        })
    return {
        "consumerPrompt": "Ruth's groceries, medicine, household items and her power bill, within the rules she and Priyank signed",
        "mandates": entries,
    }
