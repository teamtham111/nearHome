"""Deterministic validation after LLM extraction."""

from __future__ import annotations

from app.domain.enums import ConfidenceLevel, DataStatus, VerificationState
from app.domain.models import FieldCandidate


def validate_extraction_candidates(
    candidates: dict[str, list[dict]],
) -> tuple[dict[str, list[FieldCandidate]], list[str]]:
    """Validate and convert raw LLM candidates to FieldCandidate objects."""
    warnings: list[str] = []
    result: dict[str, list[FieldCandidate]] = {}

    for field_name, items in candidates.items():
        field_candidates: list[FieldCandidate] = []
        for item in items:
            value = item.get("value")
            if value is not None and field_name == "asking_price":
                try:
                    v = float(value)
                    if v <= 0:
                        warnings.append(f"asking_price must be positive; got {v}")
                        continue
                    if v < 50000:
                        warnings.append(f"asking_price {v} seems too low for HDB resale")
                except (TypeError, ValueError):
                    warnings.append("asking_price is not a valid number")
                    continue

            if value is not None and field_name == "floor_area_sqm":
                try:
                    v = float(value)
                    if v < 20 or v > 200:
                        warnings.append(f"floor_area_sqm {v} outside plausible range")
                except (TypeError, ValueError):
                    warnings.append("floor_area_sqm is not a valid number")
                    continue

            fc = FieldCandidate(
                value=value,
                raw_text=item.get("raw_text"),
                source_snippet=item.get("source_snippet"),
                source_section=item.get("source_section"),
                extraction_method=item.get("extraction_method", "llm"),
                model_confidence=item.get("model_confidence"),
                final_confidence=ConfidenceLevel(item.get("final_confidence", "NONE")),
                verification_state=VerificationState(item.get("verification_state", "UNVERIFIED")),
                status=DataStatus(item.get("status", "AVAILABLE")),
                conflicting_candidates=item.get("conflicting_candidates", []),
            )
            field_candidates.append(fc)
        if field_candidates:
            result[field_name] = field_candidates

    return result, warnings
