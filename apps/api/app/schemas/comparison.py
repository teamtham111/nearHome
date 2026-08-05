"""Pydantic API schemas."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import (
    DayType,
    JourneyMode,
    MainTransportMode,
    PriorityType,
    PropertyCategory,
    RequirementMetric,
    RequirementOperator,
)
from app.domain.models import MetricResult, RecommendationResult


class SessionCreateResponse(BaseModel):
    session_id: UUID
    demo_mode: bool
    created_at: datetime


class PriorityInput(BaseModel):
    priority_type: PriorityType
    important_location_id: UUID | None = None


class HardRequirementInput(BaseModel):
    metric: RequirementMetric
    operator: RequirementOperator
    threshold: float | str
    unit: str | None = None
    label: str | None = None
    important_location_id: UUID | None = None


class ImportantLocationInput(BaseModel):
    label: str
    place_id: str | None = None
    formatted_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    usual_day_type: DayType | None = None
    departure_time_local: time | None = None
    transport_mode: JourneyMode | None = None


class BuyerProfileInput(BaseModel):
    max_budget: float = Field(gt=0)
    priorities: list[PriorityInput] = Field(min_length=1, max_length=3)
    main_transport_mode: MainTransportMode
    hard_requirements: list[HardRequirementInput] = Field(default_factory=list)
    important_locations: list[ImportantLocationInput] = Field(default_factory=list)
    schools_matter: bool = False
    named_schools: list[str] = Field(default_factory=list, max_length=10)
    # Backward-compatible input for clients using the original single-school field.
    named_school: str | None = None

    @model_validator(mode="after")
    def normalize_named_schools(self) -> BuyerProfileInput:
        values = list(self.named_schools)
        if self.named_school:
            values.insert(0, self.named_school)
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            school = value.strip()
            key = school.casefold()
            if school and key not in seen:
                normalized.append(school)
                seen.add(key)
        if len(normalized) > 10:
            raise ValueError("A maximum of 10 named schools can be selected")
        self.named_schools = normalized
        self.named_school = normalized[0] if normalized else None
        return self

    @field_validator("priorities")
    @classmethod
    def validate_priority_count(cls, v: list[PriorityInput]) -> list[PriorityInput]:
        identifiers = [
            (p.priority_type.value, str(p.important_location_id) if p.important_location_id else None) for p in v
        ]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("The same priority cannot be selected more than once")
        return v


class ManualListingInput(BaseModel):
    asking_price: float = Field(gt=0)
    floor_area_sqm: float = Field(gt=0)
    address: str = Field(min_length=3)
    flat_type: str = Field(min_length=2)
    flat_type_raw: str | None = None
    listing_flat_subtype: str | None = None
    raw_listing_subtype: str | None = None
    flat_type_source: str | None = None
    flat_model: str | None = None
    flat_model_source: str | None = None
    subtype_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    storey_range: str | None = None
    storey_source: str | None = None
    display_name: str | None = None
    renovation_estimate: float | None = Field(default=None, ge=0)
    lease_commencement_year: int | None = None
    remaining_lease_years: float | None = Field(default=None, gt=0)
    source_url: str | None = None
    property_category: PropertyCategory = PropertyCategory.HDB


class MetricResultSchema(BaseModel):
    listing_id: UUID
    metric_name: str
    raw_value: Any
    unit: str | None
    score: float | None
    status: str
    explanation: str
    formula: str | None = None
    provenance: str

    @classmethod
    def from_domain(cls, m: MetricResult) -> MetricResultSchema:
        return cls(
            listing_id=m.listing_id,
            metric_name=m.metric_name,
            raw_value=m.raw_value,
            unit=m.unit,
            score=m.score,
            status=m.status.value,
            explanation=m.explanation,
            formula=m.formula,
            provenance=m.provenance.value,
        )


class RecommendationSchema(BaseModel):
    recommended_listing_id: UUID | None
    is_tie: bool
    is_provisional: bool
    eligible_group: str
    reason: str
    one_sentence_summary: str
    advantages: dict[str, list[str]]
    compromises: dict[str, list[str]]
    missing_information: list[str]
    confidence: str
    confidence_reasons: list[str]
    why_not_selected: dict[str, str]
    decision_hinge: str | None
    reason_codes: list[str]

    @classmethod
    def from_domain(cls, r: RecommendationResult) -> RecommendationSchema:
        return cls(
            recommended_listing_id=r.recommended_listing_id,
            is_tie=r.is_tie,
            is_provisional=r.is_provisional,
            eligible_group=r.eligible_group.value,
            reason=r.reason,
            one_sentence_summary=r.one_sentence_summary,
            advantages={k: v for k, v in r.advantages.items()},
            compromises={k: v for k, v in r.compromises.items()},
            missing_information=r.missing_information,
            confidence=r.confidence.value,
            confidence_reasons=r.confidence_reasons,
            why_not_selected=r.why_not_selected,
            decision_hinge=r.decision_hinge,
            reason_codes=r.reason_codes,
        )


class ComparisonResponse(BaseModel):
    session_id: UUID
    listing_count: int
    can_compare: bool
    immediate_metrics: list[MetricResultSchema]
    requirement_results: list[Any] = Field(default_factory=list)
    preference_scores: list[Any]
    recommendation: RecommendationSchema | None
    fair_price_status: str
    fair_price_by_listing: dict[str, Any] = Field(default_factory=dict)
    transport_by_listing: dict[str, Any] = Field(default_factory=dict)
    driving_by_listing: dict[str, Any] = Field(default_factory=dict)
    schools_by_listing: dict[str, Any] = Field(default_factory=dict)
    observations: list[Any] = Field(default_factory=list)
    journey_results: list[Any] = Field(default_factory=list)
    regular_destination_journeys: list[Any] = Field(default_factory=list)
    enrichment_summary: list[Any]
    demo_mode: bool


class SmartPasteInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    source_type: Literal["url", "text"] | None = Field(
        default=None,
        validation_alias=AliasChoices("sourceType", "source_type"),
    )
    source_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("sourceUrl", "source_url"),
    )
    raw_text: str | None = Field(
        default=None,
        validation_alias=AliasChoices("rawText", "raw_text"),
    )
    # Backward-compatible name used by older web clients.
    text: str | None = None
    source_label: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> SmartPasteInput:
        if self.raw_text is None and self.text is not None:
            self.raw_text = self.text
        if self.source_type is None:
            self.source_type = "url" if self.source_url else "text"
        if self.source_type == "url":
            if not self.source_url:
                raise ValueError("sourceUrl is required for URL extraction")
            return self
        if not self.raw_text or not self.raw_text.strip():
            raise ValueError("rawText is required for pasted-text extraction")
        if self.source_url:
            raise ValueError("sourceUrl cannot be combined with rawText")
        if len(self.raw_text) > 100_000:
            raise ValueError("Pasted text exceeds maximum length of 100000")
        return self


class ConfirmListingFromInput(BaseModel):
    listing_input_id: UUID
    asking_price: float = Field(gt=0)
    floor_area_sqm: float = Field(gt=0)
    address: str = Field(min_length=3)
    flat_type: str = Field(min_length=2)
    flat_type_raw: str | None = None
    listing_flat_subtype: str | None = None
    raw_listing_subtype: str | None = None
    flat_type_source: str | None = None
    flat_model: str | None = None
    flat_model_source: str | None = None
    subtype_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    storey_range: str | None = None
    storey_source: str | None = None
    display_name: str | None = None
    remaining_lease_years: float | None = Field(default=None, gt=0)
    property_category: PropertyCategory = PropertyCategory.HDB
    source_url: str | None = None


class GoogleRoutesDiagnosticRequest(BaseModel):
    """Development-only probe input for the live Google Routes adapter."""

    origin: tuple[float, float] = (1.3521, 103.8198)
    destination: tuple[float, float] = (1.3009, 103.8563)
    travel_mode: Literal["DRIVE", "WALK", "TRANSIT"] = "DRIVE"

    @field_validator("origin", "destination")
    @classmethod
    def validate_coordinate(cls, value: tuple[float, float]) -> tuple[float, float]:
        latitude, longitude = value
        if not (-90 <= latitude <= 90):
            raise ValueError("latitude must be between -90 and 90")
        if not (-180 <= longitude <= 180):
            raise ValueError("longitude must be between -180 and 180")
        return value


class PlacesAutocompleteResponse(BaseModel):
    suggestions: list[dict[str, str]]


class EnrichmentStatusResponse(BaseModel):
    session_id: UUID
    runs: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    demo_mode: bool
    checks: dict[str, str] | None = None


class ObservationCreate(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    value_text: str = Field(min_length=1)
