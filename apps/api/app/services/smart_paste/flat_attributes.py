"""Deterministic HDB flat-type and subtype normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FlatTypeAttributes:
    flat_type: str | None
    listing_flat_subtype: str | None
    raw_value: str | None
    flat_model: str | None = None


@dataclass(frozen=True)
class NormalisedFlatSubtype:
    raw_value: str | None
    canonical_subtype: str | None
    flat_type: str | None
    flat_model: str | None
    status: Literal["known", "unknown", "ambiguous"]


@dataclass(frozen=True)
class ResolvedFlatPropertyDetails:
    """Canonical flat fields after applying the listing provenance precedence."""

    flat_type: str | None
    raw_listing_subtype: str | None
    flat_model: str | None
    flat_type_source: str | None
    flat_model_source: str | None
    subtype_conflicts: list[dict[str, str | None]]


_ROOM_WORD = re.compile(r"\b([2-5])\s*[- ]?\s*ROOMS?\b", re.IGNORECASE)
_ROOM_CODE = re.compile(r"\b([2-5])\s*([A-Z0-9]{1,5})\b", re.IGNORECASE)
_GENERIC = {"HDB", "HDB FLAT", "FLAT", "RESALE FLAT", "RESALE HDB", "APARTMENT"}


# These are the compact labels used by listing portals. The values are the
# exact canonical strings used by the HDB transaction fixture/pipeline.
SUBTYPE_MAPPING: dict[str, dict[str, str]] = {
    "2A": {"flat_type": "2 ROOM", "flat_model": "Model A"},
    "2A2": {"flat_type": "2 ROOM", "flat_model": "Model A2"},
    "2NG": {"flat_type": "2 ROOM", "flat_model": "New Generation"},
    "2S": {"flat_type": "2 ROOM", "flat_model": "Simplified"},
    "2I": {"flat_type": "2 ROOM", "flat_model": "Improved"},
    "2STD": {"flat_type": "2 ROOM", "flat_model": "Standard"},
    "2PA": {"flat_type": "2 ROOM", "flat_model": "Premium Apartment"},
    "3A": {"flat_type": "3 ROOM", "flat_model": "Model A"},
    "3A2": {"flat_type": "3 ROOM", "flat_model": "Model A2"},
    "3NG": {"flat_type": "3 ROOM", "flat_model": "New Generation"},
    "3S": {"flat_type": "3 ROOM", "flat_model": "Simplified"},
    "3I": {"flat_type": "3 ROOM", "flat_model": "Improved"},
    "3STD": {"flat_type": "3 ROOM", "flat_model": "Standard"},
    "3PA": {"flat_type": "3 ROOM", "flat_model": "Premium Apartment"},
    "4A": {"flat_type": "4 ROOM", "flat_model": "Model A"},
    "4A2": {"flat_type": "4 ROOM", "flat_model": "Model A2"},
    "4NG": {"flat_type": "4 ROOM", "flat_model": "New Generation"},
    "4S": {"flat_type": "4 ROOM", "flat_model": "Simplified"},
    "4I": {"flat_type": "4 ROOM", "flat_model": "Improved"},
    "4STD": {"flat_type": "4 ROOM", "flat_model": "Standard"},
    "4PA": {"flat_type": "4 ROOM", "flat_model": "Premium Apartment"},
    "5A": {"flat_type": "5 ROOM", "flat_model": "Model A"},
    "5A2": {"flat_type": "5 ROOM", "flat_model": "Model A2"},
    "5NG": {"flat_type": "5 ROOM", "flat_model": "New Generation"},
    "5S": {"flat_type": "5 ROOM", "flat_model": "Simplified"},
    "5I": {"flat_type": "5 ROOM", "flat_model": "Improved"},
    "5STD": {"flat_type": "5 ROOM", "flat_model": "Standard"},
    "5PA": {"flat_type": "5 ROOM", "flat_model": "Premium Apartment"},
    "EA": {"flat_type": "EXECUTIVE", "flat_model": "Apartment"},
    "EM": {"flat_type": "EXECUTIVE", "flat_model": "Maisonette"},
}


def _compact_subtype(value: str | None) -> str | None:
    if value is None:
        return None
    compact = re.sub(r"[\s_\-/]+", "", str(value).strip().upper())
    return compact or None


def normalise_listing_subtype(value: str | None) -> NormalisedFlatSubtype:
    """Decode one compact portal subtype without guessing unknown models."""
    raw = " ".join(str(value).strip().split()) if value is not None else None
    compact = _compact_subtype(raw)
    if not compact:
        return NormalisedFlatSubtype(raw, None, None, None, "unknown")
    mapped = SUBTYPE_MAPPING.get(compact)
    if mapped:
        return NormalisedFlatSubtype(
            raw,
            compact,
            mapped["flat_type"],
            mapped["flat_model"],
            "known",
        )
    if compact in {"A", "A2", "NG", "S", "I", "STD", "PA"}:
        return NormalisedFlatSubtype(raw, compact, None, None, "ambiguous")
    room_match = re.fullmatch(r"([2-5])[A-Z0-9]{1,5}", compact)
    inferred_type = f"{room_match.group(1)} ROOM" if room_match else None
    return NormalisedFlatSubtype(raw, compact, inferred_type, None, "unknown")


# American-spelling alias retained for callers that use the rest of the
# project's normalize_* naming convention.
normalize_listing_subtype = normalise_listing_subtype


def resolve_flat_property_details(
    *,
    flat_type: str | None,
    raw_listing_subtype: str | None,
    flat_model: str | None,
    flat_type_source: str | None = None,
    flat_model_source: str | None = None,
    existing_conflicts: list[dict[str, str | None]] | None = None,
) -> ResolvedFlatPropertyDetails:
    """Resolve canonical fields without allowing a derived value to overwrite evidence.

    Explicit values are preferred when supplied by the user or listing extraction;
    subtype-derived values only fill missing fields. A disagreement is retained as
    structured evidence for the review/verified-details UI.
    """
    normalized_type = normalize_flat_type(flat_type)
    normalized_subtype = normalise_listing_subtype(
        raw_listing_subtype or normalized_type.listing_flat_subtype
    )
    raw_subtype = raw_listing_subtype or normalized_type.listing_flat_subtype

    resolved_type = normalized_type.flat_type or normalized_subtype.flat_type
    resolved_type_source = flat_type_source
    if not resolved_type_source and resolved_type:
        resolved_type_source = "derived_from_subtype" if normalized_subtype.flat_type else "unknown"

    explicit_model = str(flat_model).strip() if flat_model is not None and str(flat_model).strip() else None
    derived_model = normalized_subtype.flat_model
    resolved_model = explicit_model or derived_model
    resolved_model_source = flat_model_source
    if resolved_model_source is None and resolved_model:
        resolved_model_source = "user_confirmed" if explicit_model else "derived_from_subtype"

    conflicts = list(existing_conflicts or [])
    if explicit_model and derived_model and _canonical_compare(explicit_model) != _canonical_compare(derived_model):
        conflicts.append(
            {
                "field": "flat_model",
                "confirmed_value": explicit_model,
                "derived_from_subtype": derived_model,
                "raw_listing_subtype": raw_subtype,
                "status": "conflict",
            }
        )

    return ResolvedFlatPropertyDetails(
        flat_type=resolved_type,
        raw_listing_subtype=raw_subtype,
        flat_model=resolved_model,
        flat_type_source=resolved_type_source,
        flat_model_source=resolved_model_source,
        subtype_conflicts=conflicts,
    )


def _canonical_compare(value: str) -> str:
    return " ".join(value.strip().upper().split())


def normalize_flat_type(value: str | None) -> FlatTypeAttributes:
    """Return canonical room category while preserving a coded subtype."""
    if value is None:
        return FlatTypeAttributes(None, None, None, None)
    raw = " ".join(str(value).strip().split())
    upper = raw.upper()
    if not upper or upper in _GENERIC:
        return FlatTypeAttributes(None, None, raw or None, None)

    subtype_match = re.search(r"\(\s*([2-5][A-Z0-9]{1,5}|E[AM])\s*\)", upper)
    subtype = subtype_match.group(1) if subtype_match else None
    room_match = _ROOM_WORD.search(upper)
    if room_match:
        room_type = f"{room_match.group(1)} ROOM"
    else:
        code_match = _ROOM_CODE.search(upper)
        if not code_match:
            direct_subtype = normalise_listing_subtype(upper)
            if direct_subtype.status == "known":
                return FlatTypeAttributes(
                    direct_subtype.flat_type,
                    direct_subtype.canonical_subtype,
                    raw,
                    direct_subtype.flat_model,
                )
            for label, canonical in (
                ("MULTI-GENERATION", "MULTI-GENERATION"),
                ("MULTI GENERATION", "MULTI-GENERATION"),
                ("EXECUTIVE", "EXECUTIVE"),
                ("JUMBO", "5 ROOM"),
                ("2-ROOM FLEXI", "2 ROOM"),
                ("2 ROOM FLEXI", "2 ROOM"),
            ):
                if label in upper:
                    return FlatTypeAttributes(canonical, subtype, raw, None)
            return FlatTypeAttributes(None, subtype, raw, None)
        room_type = f"{code_match.group(1)} ROOM"
        subtype = subtype or f"{code_match.group(1)}{code_match.group(2).upper()}"

    if subtype is None:
        compact = re.search(r"\b([2-5][A-Z]{1,4})\b", upper)
        if compact:
            subtype = compact.group(1)
    subtype_attributes = normalise_listing_subtype(subtype)
    return FlatTypeAttributes(
        room_type or subtype_attributes.flat_type,
        subtype,
        raw,
        subtype_attributes.flat_model,
    )


def flat_type_source(source_type: str, source_section: str | None) -> str:
    """Map extraction evidence to the confirmation source vocabulary."""
    section = (source_section or "").lower()
    if source_type == "text":
        return "listing_text"
    if "title" in section or "heading" in section:
        return "listing_title"
    if "detail" in section or "property" in section or "summary" in section:
        return "property_details"
    if "structured" in section or "json" in section or "metadata" in section:
        return "structured_data"
    return "listing_text"
