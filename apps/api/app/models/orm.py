"""SQLAlchemy ORM models."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class ComparisonSessionORM(Base):
    __tablename__ = "comparison_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    demo_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    buyer_profile: Mapped[BuyerProfileORM | None] = relationship(back_populates="session", uselist=False)
    listings: Mapped[list[ConfirmedListingORM]] = relationship(back_populates="session")
    extractions: Mapped[list[ExtractionAttemptORM]] = relationship(back_populates="session")
    # PostgreSQL owns removal through the `ON DELETE CASCADE` foreign key. Do
    # not have SQLAlchemy null the non-nullable job.session_id first when a
    # user deletes a session.
    enrichment_jobs: Mapped[list[EnrichmentJobORM]] = relationship(
        back_populates="session", passive_deletes=True
    )


class BuyerProfileORM(Base):
    __tablename__ = "buyer_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comparison_sessions.id", ondelete="CASCADE"), unique=True
    )
    max_budget: Mapped[float] = mapped_column(Float, nullable=False)
    main_transport_mode: Mapped[str] = mapped_column(String(50), nullable=False)
    schools_matter: Mapped[bool] = mapped_column(Boolean, default=False)
    named_schools_json: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    named_school: Mapped[str | None] = mapped_column(String(255))
    priorities_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    session: Mapped[ComparisonSessionORM] = relationship(back_populates="buyer_profile")
    hard_requirements: Mapped[list[HardRequirementORM]] = relationship(back_populates="buyer_profile")
    important_locations: Mapped[list[ImportantLocationORM]] = relationship(back_populates="buyer_profile")


class HardRequirementORM(Base):
    __tablename__ = "hard_requirements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    buyer_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buyer_profiles.id", ondelete="CASCADE")
    )
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    operator: Mapped[str] = mapped_column(String(10), nullable=False)
    threshold_number: Mapped[float | None] = mapped_column(Float)
    threshold_text: Mapped[str | None] = mapped_column(String(100))
    unit: Mapped[str | None] = mapped_column(String(30))
    label: Mapped[str | None] = mapped_column(String(255))
    important_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("important_locations.id", ondelete="SET NULL")
    )

    buyer_profile: Mapped[BuyerProfileORM] = relationship(back_populates="hard_requirements")


class ImportantLocationORM(Base):
    __tablename__ = "important_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    buyer_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buyer_profiles.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    place_id: Mapped[str | None] = mapped_column(String(255))
    formatted_address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    usual_day_type: Mapped[str | None] = mapped_column(String(20))
    departure_time_local: Mapped[time | None] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Singapore")
    transport_mode: Mapped[str | None] = mapped_column(String(30))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)

    buyer_profile: Mapped[BuyerProfileORM] = relationship(back_populates="important_locations")

    __table_args__ = (Index("ix_important_locations_buyer_profile", "buyer_profile_id"),)


class ExtractionAttemptORM(Base):
    __tablename__ = "extraction_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comparison_sessions.id", ondelete="CASCADE")
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    cleaned_text: Mapped[str | None] = mapped_column(Text)
    source_label: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    character_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(50))
    model_name: Mapped[str | None] = mapped_column(String(100))
    model_version: Mapped[str | None] = mapped_column(String(50))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    schema_version: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ComparisonSessionORM] = relationship(back_populates="extractions")
    listing_inputs: Mapped[list[ListingInputORM]] = relationship(back_populates="extraction")


class ListingInputORM(Base):
    __tablename__ = "listing_inputs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comparison_sessions.id", ondelete="CASCADE")
    )
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_attempts.id", ondelete="SET NULL")
    )
    raw_text: Mapped[str | None] = mapped_column(Text)
    cleaned_text: Mapped[str | None] = mapped_column(Text)
    candidates_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    extraction_warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    agent_claims: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    source_label: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    property_category: Mapped[str] = mapped_column(String(30), default="HDB")
    input_method: Mapped[str] = mapped_column(String(30), default="manual")
    pipeline_version: Mapped[str | None] = mapped_column(String(50))
    model_name: Mapped[str | None] = mapped_column(String(100))
    model_version: Mapped[str | None] = mapped_column(String(50))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    schema_version: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    extraction: Mapped[ExtractionAttemptORM | None] = relationship(back_populates="listing_inputs")
    confirmed_listing: Mapped[ConfirmedListingORM | None] = relationship(back_populates="listing_input")


class ConfirmedListingORM(Base):
    __tablename__ = "confirmed_listings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comparison_sessions.id", ondelete="CASCADE")
    )
    listing_input_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listing_inputs.id", ondelete="SET NULL")
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    asking_price: Mapped[float] = mapped_column(Float, nullable=False)
    floor_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_address_key: Mapped[str] = mapped_column(String(512), index=True)
    flat_type: Mapped[str] = mapped_column(String(50), nullable=False)
    flat_type_raw: Mapped[str | None] = mapped_column(String(100))
    listing_flat_subtype: Mapped[str | None] = mapped_column(String(50))
    raw_listing_subtype: Mapped[str | None] = mapped_column(String(50))
    flat_type_source: Mapped[str | None] = mapped_column(String(50))
    flat_model: Mapped[str | None] = mapped_column(String(100))
    flat_model_source: Mapped[str | None] = mapped_column(String(50))
    subtype_conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    storey_range: Mapped[str | None] = mapped_column(String(50))
    storey_source: Mapped[str | None] = mapped_column(String(50))
    property_category: Mapped[str] = mapped_column(String(30), default="HDB")
    renovation_estimate: Mapped[float | None] = mapped_column(Float)
    lease_commencement_year: Mapped[int | None] = mapped_column(Integer)
    remaining_lease_years: Mapped[float | None] = mapped_column(Float)
    remaining_lease_months: Mapped[int | None] = mapped_column(Integer)
    remaining_lease_source: Mapped[str | None] = mapped_column(String(50))
    remaining_lease_confidence: Mapped[str | None] = mapped_column(String(20))
    remaining_lease_as_of_date: Mapped[date | None] = mapped_column(Date)
    remaining_lease_status: Mapped[str] = mapped_column(String(50), default="NOT_PROVIDED_BY_USER")
    source_url: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    field_provenance_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)

    session: Mapped[ComparisonSessionORM] = relationship(back_populates="listings")
    listing_input: Mapped[ListingInputORM | None] = relationship(back_populates="confirmed_listing")
    observations: Mapped[list[ObservationORM]] = relationship(back_populates="listing")
    enrichment_runs: Mapped[list[EnrichmentRunORM]] = relationship(back_populates="listing")
    enriched_fields: Mapped[list[EnrichedFieldORM]] = relationship(back_populates="listing")
    journey_estimates: Mapped[list[JourneyEstimateORM]] = relationship(back_populates="listing")
    enrichment_jobs: Mapped[list[EnrichmentJobORM]] = relationship(
        back_populates="listing", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_confirmed_listings_session", "session_id"),
        UniqueConstraint("session_id", "normalized_address_key", "asking_price", name="uq_listing_dup"),
    )


class ObservationORM(Base):
    __tablename__ = "observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confirmed_listings.id", ondelete="CASCADE")
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="USER")
    verification_state: Mapped[str] = mapped_column(String(50), default="UNVERIFIED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    listing: Mapped[ConfirmedListingORM] = relationship(back_populates="observations")


class EnrichmentRunORM(Base):
    __tablename__ = "enrichment_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confirmed_listings.id", ondelete="CASCADE")
    )
    enrichment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    listing: Mapped[ConfirmedListingORM] = relationship(back_populates="enrichment_runs")

    __table_args__ = (Index("ix_enrichment_runs_listing_type", "listing_id", "enrichment_type"),)


class EnrichmentJobORM(Base):
    """Durable state for one independently-dispatched enrichment request."""

    __tablename__ = "enrichment_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comparison_sessions.id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confirmed_listings.id", ondelete="CASCADE"), nullable=True
    )
    job_type: Mapped[str] = mapped_column(String(80), nullable=False, default="SESSION_ENRICHMENT")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    progress_stage: Mapped[str] = mapped_column(String(100), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    internal_error_detail: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    session: Mapped[ComparisonSessionORM] = relationship(back_populates="enrichment_jobs")
    listing: Mapped[ConfirmedListingORM | None] = relationship(back_populates="enrichment_jobs")

    __table_args__ = (
        Index("ix_enrichment_jobs_session", "session_id"),
        Index("ix_enrichment_jobs_listing", "listing_id"),
        Index("ix_enrichment_jobs_status", "status"),
        Index("ix_enrichment_jobs_created_at", "created_at"),
    )


class EnrichedFieldORM(Base):
    __tablename__ = "enriched_fields"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confirmed_listings.id", ondelete="CASCADE")
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str | None] = mapped_column(String(100))
    source_version: Mapped[str | None] = mapped_column(String(50))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[str] = mapped_column(String(20), default="NONE")
    assumptions_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    provenance: Mapped[str] = mapped_column(String(50), default="CALCULATED")

    listing: Mapped[ConfirmedListingORM] = relationship(back_populates="enriched_fields")

    __table_args__ = (Index("ix_enriched_fields_listing_name", "listing_id", "field_name"),)


class JourneyEstimateORM(Base):
    __tablename__ = "journey_estimates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confirmed_listings.id", ondelete="CASCADE")
    )
    important_location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("important_locations.id", ondelete="CASCADE")
    )
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_day_type: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_time_local: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Singapore")
    resolved_departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    difference_from_fastest_seconds: Mapped[int | None] = mapped_column(Integer)
    is_fastest: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(50))
    provider_status: Mapped[str | None] = mapped_column(String(100))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    listing: Mapped[ConfirmedListingORM] = relationship(back_populates="journey_estimates")

    __table_args__ = (
        Index("ix_journey_estimates_listing_location_mode", "listing_id", "important_location_id", "mode"),
    )


class RecommendationTraceORM(Base):
    __tablename__ = "recommendation_traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comparison_sessions.id", ondelete="CASCADE"), index=True
    )
    trace_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(20))
    scoring_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExternalApiCacheORM(Base):
    __tablename__ = "external_api_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    cache_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    request_summary: Mapped[str | None] = mapped_column(Text)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HdbCarparkORM(Base):
    """Official static HDB carpark information, normalised from data.gov.sg."""

    __tablename__ = "hdb_carparks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    carpark_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    carpark_type: Mapped[str | None] = mapped_column(String(80))
    source_carpark_type: Mapped[str | None] = mapped_column(String(120))
    parking_system_type: Mapped[str | None] = mapped_column(String(80))
    short_term_parking: Mapped[str | None] = mapped_column(String(120))
    free_parking: Mapped[str | None] = mapped_column(String(120))
    night_parking: Mapped[str | None] = mapped_column(String(120))
    carpark_decks: Mapped[int | None] = mapped_column(Integer)
    gantry_height_m: Mapped[float | None] = mapped_column(Float)
    basement_indicator: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="data.gov.sg")
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_hdb_carparks_geo", "latitude", "longitude"),
        Index("ix_hdb_carparks_number", "carpark_no"),
    )


class CarparkAvailabilitySnapshotORM(Base):
    """Point-in-time official availability observations; NULL is never zero."""

    __tablename__ = "carpark_availability_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    carpark_no: Mapped[str] = mapped_column(String(40), nullable=False)
    lot_type: Mapped[str] = mapped_column(String(10), nullable=False)
    total_lots: Mapped[int | None] = mapped_column(Integer)
    available_lots: Mapped[int | None] = mapped_column(Integer)
    availability_pct: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        Index("ix_carpark_availability_number_time", "carpark_no", "observed_at"),
        UniqueConstraint("carpark_no", "lot_type", "observed_at", name="uq_carpark_availability_snapshot"),
    )


class ListingCarparkMatchORM(Base):
    """Evidence linking a confirmed listing to nearby official carparks."""

    __tablename__ = "listing_carpark_matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confirmed_listings.id", ondelete="CASCADE"), nullable=False
    )
    carpark_no: Mapped[str] = mapped_column(String(40), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    haversine_distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    routed_walk_distance_m: Mapped[float | None] = mapped_column(Float)
    routed_walk_minutes: Mapped[float | None] = mapped_column(Float)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    match_type: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_listing_carpark_matches_listing", "listing_id"),
        UniqueConstraint("listing_id", "carpark_no", name="uq_listing_carpark_match"),
    )


class ParkingMetricORM(Base):
    """Persisted deterministic home-parking score and its structured evidence."""

    __tablename__ = "parking_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confirmed_listings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    score: Mapped[float | None] = mapped_column(Float)
    score_status: Mapped[str] = mapped_column(String(40), nullable=False)
    metric_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    score_version: Mapped[str] = mapped_column(String(40), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_parking_metrics_listing", "listing_id"),)
