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
    "allowed_merchants": ["corner_market"],
    "allowed_categories": ["grocery", "pharmacy"],
    "blocked_categories": ["gift_card", "prepaid_card", "wire", "crypto", "lottery"],
    "languages": ["es", "en", "hi"],
    "valid_from": "2026-09-01",
    "valid_to": "2026-12-31",
}

MONTHLY_BASELINE = 142.10


def canonical_mandate(mandate: dict) -> dict:
    return {key: value for key, value in mandate.items() if key != "passkey"}


def mandate_hash(mandate: dict) -> bytes:
    return hashlib.sha256(jcs.canonicalize(canonical_mandate(mandate))).digest()
