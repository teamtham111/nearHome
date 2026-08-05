"""Stable domain dataclasses used across services and engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from app.domain.enums import (
    ConfidenceLevel,
    DataStatus,
    DayType,
    JourneyMode,
    ListingGroup,
    MainTransportMode,
    PriorityType,
    PropertyCategory,
    Provenance,
    RecommendationConfidence,
    RequirementMetric,
    RequirementOperator,
    RequirementStatus,
    VerificationState,
)


@dataclass
class Priority:
    priority_type: PriorityType
    rank: int  # 1 = highest
    weight: float
    important_location_id: UUID | None = None

    @property
    def identifier(self) -> str:
        if self.priority_type == PriorityType.IMPORTANT_LOCATION_JOURNEY:
            if self.important_location_id is None:
                raise ValueError("Important location journey priority requires location ID")
            return f"important_location_journey:{self.important_location_id}"
        return self.priority_type.value


DEFAULT_PRIORITY_WEIGHTS = {1: 0.45, 2: 0.35, 3: 0.20}


def build_priorities(priority_specs: list[tuple[PriorityType, UUID | None]]) -> list[Priority]:
    """Build ranked priorities with default 45/35/20 weights."""
    if len(priority_specs) > 3:
        raise ValueError("Maximum three priorities allowed")
    identifiers = [(priority_type, location_id) for priority_type, location_id in priority_specs]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("The same priority cannot be selected more than once")
    return [
        Priority(
            priority_type=pt,
            rank=idx + 1,
            weight=DEFAULT_PRIORITY_WEIGHTS[idx + 1],
            important_location_id=loc_id,
        )
        for idx, (pt, loc_id) in enumerate(priority_specs)
    ]


@dataclass
class HardRequirement:
    requirement_id: UUID | None
    metric: RequirementMetric
    operator: RequirementOperator
    threshold: float | str
    unit: str | None = None
    label: str | None = None
    important_location_id: UUID | None = None


@dataclass
class ImportantLocation:
    important_location_id: UUID
    label: str
    place_id: str | None
    formatted_address: str | None
    latitude: float | None
    longitude: float | None
    usual_day_type: DayType | None
    departure_time_local: time | None
    timezone: str = "Asia/Singapore"
    transport_mode: JourneyMode | None = None
    confirmed_at: datetime | None = None
    is_complete: bool = False


@dataclass
class BuyerProfile:
    max_budget: float
    priorities: list[Priority]
    main_transport_mode: MainTransportMode
    hard_requirements: list[HardRequirement] = field(default_factory=list)
    important_locations: list[ImportantLocation] = field(default_factory=list)
    schools_matter: bool = False
    named_schools: list[str] = field(default_factory=list)
    # Kept for compatibility with older callers and persisted profiles.
    named_school: str | None = None

    @property
    def school_names(self) -> list[str]:
        values = list(self.named_schools)
        if self.named_school:
            values.insert(0, self.named_school)
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            school = value.strip()
            key = school.casefold()
            if school and key not in seen:
                result.append(school)
                seen.add(key)
        return result


@dataclass
class ComparisonSession:
    session_id: UUID
    created_at: datetime
    updated_at: datetime
    buyer_profile: BuyerProfile | None = None
    listing_ids: list[UUID] = field(default_factory=list)
    demo_mode: bool = False


@dataclass
class FieldCandidate:
    value: Any
    raw_text: str | None
    source_snippet: str | None
    source_section: str | None
    extraction_method: str
    model_confidence: str | None
    final_confidence: ConfidenceLevel
    verification_state: VerificationState
    status: DataStatus
    conflicting_candidates: list[Any] = field(default_factory=list)


@dataclass
class ExtractionAttempt:
    extraction_id: UUID
    session_id: UUID
    original_text: str
    cleaned_text: str | None
    source_label: str | None
    source_url: str | None
    character_count: int
    content_hash: str
    pipeline_version: str
    model_name: str | None
    model_version: str | None
    prompt_version: str | None
    schema_version: str | None
    created_at: datetime
    status: str


@dataclass
class ListingInput:
    listing_input_id: UUID
    session_id: UUID
    extraction_id: UUID | None
    raw_text: str | None
    cleaned_text: str | None
    candidates: dict[str, list[FieldCandidate]]
    extraction_warnings: list[str]
    agent_claims: list[dict[str, Any]]
    source_label: str | None
    source_url: str | None
    property_category: PropertyCategory
    pipeline_version: str | None
    model_name: str | None
    model_version: str | None
    prompt_version: str | None
    schema_version: str | None
    input_method: str  # smart_paste | manual


@dataclass
class ListingFieldProvenance:
    field_name: str
    value: Any
    provenance: Provenance
    status: DataStatus
    source_snippet: str | None = None
    corrected_by_user: bool = False


@dataclass
class ConfirmedListing:
    listing_id: UUID
    session_id: UUID
    display_name: str
    asking_price: float
    floor_area_sqm: float
    address: str
    flat_type: str
    flat_type_raw: str | None = None
    listing_flat_subtype: str | None = None
    raw_listing_subtype: str | None = None
    flat_type_source: str | None = None
    property_category: PropertyCategory = PropertyCategory.HDB
    renovation_estimate: float | None = None
    lease_commencement_year: int | None = None
    remaining_lease_years: float | None = None
    # Canonical lease value. ``remaining_lease_years`` remains for API/UI
    # compatibility and is derived from these months during enrichment.
    remaining_lease_months: int | None = None
    remaining_lease_source: str | None = None
    remaining_lease_confidence: str | None = None
    remaining_lease_as_of_date: date | None = None
    remaining_lease_status: DataStatus = DataStatus.NOT_PROVIDED_BY_USER
    source_url: str | None = None
    confirmed_at: datetime | None = None
    field_provenance: list[ListingFieldProvenance] = field(default_factory=list)
    listing_input_id: UUID | None = None
    # Optional HDB attributes used when a source provides them. They are kept
    # separate from the required confirmation fields so older saved listings
    # remain valid and missing values never become fabricated similarity data.
    flat_model: str | None = None
    flat_model_source: str | None = None
    subtype_conflicts: list[dict[str, Any]] = field(default_factory=list)
    storey_range: str | None = None
    storey_source: str | None = None


@dataclass
class Observation:
    observation_id: UUID
    listing_id: UUID
    category: str
    value_text: str
    source: str
    verification_state: VerificationState
    created_at: datetime
    updated_at: datetime


@dataclass
class EnrichedField:
    field_name: str
    value: Any
    status: DataStatus
    source: str | None
    source_version: str | None
    retrieved_at: datetime | None
    confidence: ConfidenceLevel
    assumptions: list[str] = field(default_factory=list)
    provenance: Provenance = Provenance.CALCULATED


@dataclass
class EnrichmentRun:
    enrichment_run_id: UUID
    listing_id: UUID
    enrichment_type: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None = None


@dataclass
class JourneyEstimate:
    journey_estimate_id: UUID
    listing_id: UUID
    important_location_id: UUID
    mode: JourneyMode
    requested_day_type: DayType
    requested_time_local: time
    timezone: str
    resolved_departure_at: datetime | None
    duration_seconds: int | None
    difference_from_fastest_seconds: int | None
    is_fastest: bool | None
    status: DataStatus
    provider: str
    provider_status: str | None
    retrieved_at: datetime | None


@dataclass
class MetricResult:
    listing_id: UUID
    metric_name: str
    raw_value: Any
    unit: str | None
    score: float | None
    status: DataStatus
    explanation: str
    context_id: UUID | None = None
    mode: JourneyMode | None = None
    assumption: str | None = None
    coverage: str | None = None
    formula: str | None = None
    provenance: Provenance = Provenance.CALCULATED


@dataclass
class RequirementResult:
    listing_id: UUID
    requirement: HardRequirement
    status: RequirementStatus
    actual_value: Any
    threshold: Any
    difference_from_threshold: float | None
    source_metric: str
    explanation: str
    rule_version: str


@dataclass
class PreferenceScore:
    listing_id: UUID
    # `total_score` is retained as a compatibility alias for persisted traces.
    # Both fields are the same absolute 0–100 fit score; neither is a rank.
    total_score: float
    sub_scores: dict[str, float]
    weights: dict[str, float]
    raw_values: dict[str, Any]
    coverage: float
    is_tie_candidate: bool
    trade_off_flags: list[str]
    scoring_version: str
    overall_fit_score: float | None = None
    rank: int | None = None


@dataclass
class RecommendationResult:
    recommended_listing_id: UUID | None
    is_tie: bool
    is_provisional: bool
    eligible_group: ListingGroup
    reason: str
    one_sentence_summary: str
    advantages: dict[str, list[str]]  # listing_id -> advantages
    compromises: dict[str, list[str]]
    missing_information: list[str]
    confidence: RecommendationConfidence
    confidence_reasons: list[str]
    why_not_selected: dict[str, str]
    decision_hinge: str | None
    reason_codes: list[str]
    rule_version: str
    scoring_version: str


@dataclass
class RecommendationTrace:
    trace_id: UUID
    session_id: UUID
    requirement_results: list[RequirementResult]
    preference_scores: list[PreferenceScore]
    recommendation: RecommendationResult
    listing_groups: dict[str, ListingGroup]
    created_at: datetime
    inputs_hash: str
