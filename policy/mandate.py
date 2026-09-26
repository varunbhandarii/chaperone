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

# Display names and MCCs for GET /mandate/visa. Caps come from the card rules when present.
VISA_STORES = {
    "corner_market": ("Corner Market", "Grocery Stores, Supermarkets", "5411", "Groceries for Ruth, per purchase"),
    "parkside_pharmacy": ("Parkside Pharmacy", "Drug Stores and Pharmacies", "5912", "Medicine for Ruth, per purchase"),
    "main_street_home": ("Main Street Home", "Discount Stores", "5251", "Household items for Ruth, per purchase"),
    "peachtree_power": ("Peachtree Power", "Utilities", "4900", "Ruth's power bill"),
}

MONTHLY_BASELINE = 142.10


def fill_v2(mandate: dict) -> dict:
    """Show the new stores, categories and billers even when a v1 file was signed earlier.

    The signed file is not rewritten. A later passkey sign stores the filled fields.
    """
    filled = dict(mandate)
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
    for merchant_id in mandate.get("allowed_merchants") or []:
        if merchant_id not in VISA_STORES:
            continue
        name, category, mcc, description = VISA_STORES[merchant_id]
        if merchant_id == "peachtree_power":
            bill = next((row for row in (mandate.get("billers") or []) if row.get("merchant_id") == merchant_id), {})
            amount = f"{float(bill.get('monthly_cap') or 0):.2f}"
        else:
            amount = f"{float(caps.get(mcc) or mandate.get('per_purchase_cap') or 0):.2f}"
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
