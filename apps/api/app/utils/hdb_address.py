"""Canonical Singapore HDB block-and-street address identity.

This module intentionally returns an exact structural key rather than doing
fuzzy or substring matching. A block and its canonical street name must both
match before a transaction can be used as evidence for a listing.
"""

from __future__ import annotations

import re
import unicodedata

_STREET_ALIASES = {
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "RD": "ROAD",
    "DR": "DRIVE",
    "ST": "STREET",
    "CTRL": "CENTRAL",
    "CRES": "CRESCENT",
    "CL": "CLOSE",
    "JLN": "JALAN",
    "LOR": "LORONG",
    "LN": "LANE",
    "PL": "PLACE",
    "TER": "TERRACE",
    "UPP": "UPPER",
}
_APOSTROPHES = "'\u2018\u2019\u02bc\u02bb\u0060"


def normalize_hdb_address(address: str, postal_code: str | None = None) -> tuple[str | None, str | None, str | None]:
    """Return ``(block, street, postal_code)`` in canonical form.

    Postal codes embedded in a pasted address are removed from the street
    identity. Apostrophes are removed (so ``KING GEORGE'S`` and
    ``KING GEORGE’S`` agree), while other punctuation becomes whitespace.
    Street suffix aliases are expanded to one canonical spelling.
    """
    text = unicodedata.normalize("NFKC", str(address or "")).upper()
    for apostrophe in _APOSTROPHES:
        text = text.replace(apostrophe, "")
    text = re.sub(r"[^A-Z0-9\- ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = text.split()
    if tokens and tokens[0] in {"BLK", "BLOCK"}:
        tokens.pop(0)
    if not tokens or not re.fullmatch(r"\d+[A-Z]?|\d+-[A-Z]", tokens[0]):
        return None, None, _normalize_postal(postal_code)

    block = tokens.pop(0).replace("-", "")
    if tokens and len(tokens[0]) == 1 and tokens[0].isalpha():
        block += tokens.pop(0)

    street_text = re.sub(r"\b\d{6}\b", " ", " ".join(tokens))
    # OneMap/listing portals commonly append the country after the street.
    # It is not part of the HDB street identity and must not make the same
    # address fail exact geocoder/transaction matching.
    street_tokens = [
        _STREET_ALIASES.get(token, token) for token in street_text.split() if token != "SINGAPORE"
    ]
    street = " ".join(street_tokens).strip() or None
    return block, street, _normalize_postal(postal_code)


def canonical_hdb_address_key(address: str, postal_code: str | None = None) -> tuple[str, str] | None:
    """Return the exact comparable key ``(block, canonical_street)``."""
    block, street, _ = normalize_hdb_address(address, postal_code)
    return (block, street) if block and street else None


def canonical_hdb_parts(block: str | None, street: str | None) -> tuple[str, str] | None:
    """Canonicalize separate HDB transaction block/street columns."""
    if not block or not street:
        return None
    return canonical_hdb_address_key(f"{block} {street}")


def _normalize_postal(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits if len(digits) == 6 else None
