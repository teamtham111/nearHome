"""Reconcile Smart Paste candidates into suggested confirmation values."""

from __future__ import annotations

from app.domain.enums import ConfidenceLevel, DataStatus
from app.domain.models import FieldCandidate


def reconcile_candidates(
    candidates: dict[str, list[FieldCandidate]],
) -> tuple[dict[str, object], list[str], dict[str, list[dict]]]:
    """Pick primary value per field and surface conflicts for confirmation UI."""
    suggested: dict[str, object] = {}
    warnings: list[str] = []
    evidence: dict[str, list[dict]] = {}

    for field, cands in candidates.items():
        evidence[field] = [
            {
                "value": c.value,
                "source_snippet": c.source_snippet,
                "raw_text": c.raw_text,
                "source_section": c.source_section,
                "extraction_method": c.extraction_method,
                "final_confidence": c.final_confidence.value,
                "status": c.status.value,
            }
            for c in cands
        ]
        accepted = [c for c in cands if c.status != DataStatus.EXTRACTION_UNCERTAIN]
        if not accepted:
            continue

        primary = max(
            accepted,
            key=lambda c: (
                _confidence_rank(c.final_confidence),
                c.status == DataStatus.AVAILABLE,
            ),
        )
        suggested[field] = primary.value

        conflicts = [c for c in accepted if c.value != primary.value]
        if conflicts:
            warnings.append(
                f"Multiple values for {field}: using {primary.value!r} "
                f"({primary.final_confidence.value} confidence)"
            )

    return suggested, warnings, evidence


def _confidence_rank(level: ConfidenceLevel) -> int:
    order = {
        ConfidenceLevel.HIGH: 4,
        ConfidenceLevel.MEDIUM: 3,
        ConfidenceLevel.LOW: 2,
        ConfidenceLevel.NONE: 1,
    }
    return order.get(level, 0)
