"""Stable HDB block identity shared by the block registry and listings.

A block number alone is not unique in Singapore (``Blk 1`` exists on many
streets), so a block is identified by its block number *and* a normalised
street name. HDB's own datasets abbreviate street words (``BT BATOK``,
``BEDOK NTH``, ``C'WEALTH``) while listing portals and OneMap spell them out,
so this module expands those HDB abbreviations after the shared
``canonical_hdb_address_key`` normalisation. The expansion is deliberately
limited to this identity key; it does not change how fair-price transaction
matching canonicalises addresses.
"""

from __future__ import annotations

from app.utils.hdb_address import canonical_hdb_address_key, canonical_hdb_parts

# HDB street abbreviations that the shared canonicaliser leaves untouched.
# ``ST`` is intentionally absent: the shared canonicaliser already maps it to
# STREET; a leading STREET is turned into SAINT in ``_expand_street``.
_HDB_STREET_EXPANSIONS = {
    "BT": "BUKIT",
    "KG": "KAMPONG",
    "TG": "TANJONG",
    "NTH": "NORTH",
    "STH": "SOUTH",
    "CWEALTH": "COMMONWEALTH",
    "MKT": "MARKET",
    "PK": "PARK",
    "HTS": "HEIGHTS",
    "GDNS": "GARDENS",
    "CTR": "CENTRE",
    "SQ": "SQUARE",
    "BLVD": "BOULEVARD",
    "HWY": "HIGHWAY",
    "WK": "WALK",
}


def _expand_street(street: str) -> str:
    tokens = [_HDB_STREET_EXPANSIONS.get(token, token) for token in street.split()]
    # The shared canonicaliser reads "ST" as STREET; a street name never
    # *starts* with "Street", so a leading one is "Saint" (ST. GEORGE'S RD).
    if len(tokens) > 1 and tokens[0] == "STREET":
        tokens[0] = "SAINT"
    return " ".join(tokens)


def block_key_from_parts(block: str | None, street: str | None) -> str | None:
    """Return ``"<BLOCK>|<STREET>"`` for separate block/street columns."""
    parts = canonical_hdb_parts(block, street)
    if parts is None:
        return None
    return f"{parts[0]}|{_expand_street(parts[1])}"


def block_key_from_address(address: str | None) -> str | None:
    """Return the block key for a free-text HDB address, or ``None``."""
    parts = canonical_hdb_address_key(address or "")
    if parts is None:
        return None
    return f"{parts[0]}|{_expand_street(parts[1])}"


def split_block_key(block_key: str) -> tuple[str, str]:
    block, _, street = block_key.partition("|")
    return block, street


def _is_abbreviation(short: str, full: str) -> bool:
    """True when ``short`` reads as an abbreviation of ``full``.

    Requires the same first letter and every letter of ``short`` to appear in
    order within ``full`` (``NTH`` → ``NORTH``). Numbers must match exactly.
    """
    if short == full:
        return True
    if not short or not full or short[0] != full[0] or short.isdigit() or full.isdigit():
        return False
    position = 0
    for char in short:
        position = full.find(char, position)
        if position < 0:
            return False
        position += 1
    return len(short) < len(full)


def streets_equivalent(left: str, right: str) -> bool:
    """Token-wise street comparison tolerant of unlisted HDB abbreviations.

    Used only when matching an HDB registry row to a OneMap geocoder result
    for the *same block number*; the block number must already agree.
    """
    left_tokens = _expand_street(left).split()
    right_tokens = _expand_street(right).split()
    if len(left_tokens) != len(right_tokens):
        return False
    return all(_is_abbreviation(a, b) or _is_abbreviation(b, a) for a, b in zip(left_tokens, right_tokens, strict=True))
