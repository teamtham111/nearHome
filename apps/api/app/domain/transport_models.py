"""Shared result/evidence types for the rebuilt Public Transport and Driving models.

These types are intentionally separate from the generic `MetricResult` in
`app.domain.models` — they carry richer, transport-specific evidence
(strengths/limitations/evidence lists) that only these two models need.
Each engine's `engine.py` orchestrator converts `ComponentResult` ->
`MetricResult` at the boundary so the rest of the app (requirements,
preference scoring, journeys) keeps its existing contract untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import ComponentStatus, Provenance


@dataclass
class ComponentResult:
    """Result of scoring a single Public Transport / Driving component.

    `score` and `value` MUST be None whenever `status` is one of
    NOT_ASSESSED, PROVIDER_ERROR, or INSUFFICIENT_DATA — a component must
    never carry a fallback/neutral number when its required data is absent.
    """

    name: str
    value: Any
    score: float | None
    weight: float
    status: ComponentStatus
    explanation: str
    strengths: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source: str | None = None
    provenance: Provenance = Provenance.CALCULATED
    confidence: str = "unavailable"

    def __post_init__(self) -> None:
        if self.status in (
            ComponentStatus.NOT_ASSESSED,
            ComponentStatus.PROVIDER_ERROR,
            ComponentStatus.INSUFFICIENT_DATA,
        ):
            # Enforce at construction time so no call site can accidentally
            # attach a fallback score to an unavailable component.
            self.value = None
            self.score = None

    @property
    def is_assessed(self) -> bool:
        return self.status in (ComponentStatus.CALCULATED, ComponentStatus.ESTIMATED) and self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "score": self.score,
            "weight": self.weight,
            "status": self.status.value,
            "explanation": self.explanation,
            "strengths": self.strengths,
            "limitations": self.limitations,
            "evidence": self.evidence,
            "source": self.source,
            "provenance": self.provenance.value,
            "confidence": self.confidence,
        }


def not_assessed(name: str, weight: float, reason: str, limitations: list[str] | None = None) -> ComponentResult:
    """Shorthand for the standard "data unavailable" component result."""
    return ComponentResult(
        name=name,
        value=None,
        score=None,
        weight=weight,
        status=ComponentStatus.NOT_ASSESSED,
        explanation=reason,
        strengths=[],
        limitations=limitations or [reason],
        evidence=[],
        source=None,
        provenance=Provenance.CALCULATED,
        confidence="unavailable",
    )


def provider_error(name: str, weight: float, reason: str) -> ComponentResult:
    """Shorthand for a routing/data-provider failure — never fabricate a route."""
    return ComponentResult(
        name=name,
        value=None,
        score=None,
        weight=weight,
        status=ComponentStatus.PROVIDER_ERROR,
        explanation=reason,
        strengths=[],
        limitations=[reason],
        evidence=[],
        source=None,
        provenance=Provenance.CALCULATED,
        confidence="unavailable",
    )


@dataclass
class ModelRollup:
    """Overall rollup for a multi-component model (Public Transport / Driving).

    `overall_score` is the number allowed to influence the recommendation —
    it is None unless `counts_toward_recommendation` is True. `display_score`
    is always populated when at least one component was assessed, purely for
    UI display, and must be presented as partial/non-comparable when
    `is_complete` is False. `unrounded_score` supports shortlist ranking while
    the UI presents the rounded display score.
    """

    components: list[ComponentResult]
    display_score: float | None
    unrounded_score: float | None
    overall_score: float | None
    is_complete: bool
    counts_toward_recommendation: bool
    coverage_ratio: float
    assessed_component_names: list[str]
    excluded_component_names: list[str]
    warnings: list[str] = field(default_factory=list)


def build_rollup(
    components: list[ComponentResult],
    min_core_weight_coverage: float,
) -> ModelRollup:
    """Combine component results into a model-level score with recommendation gating.

    Renormalises only across *assessed* components (weight-average), but —
    unlike the old implementation — never silently presents that as complete.
    `overall_score` (the number fed to PreferenceScoringEngine) is only
    populated when the assessed weight covers at least
    `min_core_weight_coverage` of the total possible weight.
    """
    total_weight = sum(c.weight for c in components)
    assessed = [c for c in components if c.is_assessed]
    assessed_weight = sum(c.weight for c in assessed)
    coverage_ratio = (assessed_weight / total_weight) if total_weight else 0.0

    unrounded_score = (
        sum(c.score * c.weight for c in assessed if c.score is not None) / assessed_weight
        if assessed_weight
        else None
    )
    display_score = round(unrounded_score, 1) if unrounded_score is not None else None

    is_complete = len(assessed) == len(components) and len(components) > 0
    counts_toward_recommendation = bool(assessed) and coverage_ratio >= min_core_weight_coverage
    overall_score = display_score if counts_toward_recommendation else None

    warnings: list[str] = []
    if assessed and not is_complete:
        excluded = [c.name for c in components if not c.is_assessed]
        warnings.append(
            f"Partial result — {', '.join(excluded)} could not be assessed; "
            "remaining component weights were renormalised for display only."
        )
    if assessed and not counts_toward_recommendation:
        warnings.append(
            "Assessed data coverage is below the minimum required to influence "
            "the recommendation — this score is shown for information only."
        )

    return ModelRollup(
        components=components,
        display_score=display_score,
        unrounded_score=unrounded_score,
        overall_score=overall_score,
        is_complete=is_complete,
        counts_toward_recommendation=counts_toward_recommendation,
        coverage_ratio=round(coverage_ratio, 2),
        assessed_component_names=[c.name for c in assessed],
        excluded_component_names=[c.name for c in components if not c.is_assessed],
        warnings=warnings,
    )
