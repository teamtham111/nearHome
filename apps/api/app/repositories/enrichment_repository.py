"""Enrichment data persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.enums import EnrichmentStatus
from app.models.orm import ConfirmedListingORM, EnrichedFieldORM, EnrichmentRunORM, JourneyEstimateORM


class EnrichmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_run(
        self,
        listing_id: UUID,
        enrichment_type: str,
        status: str,
        error_message: str | None = None,
    ) -> EnrichmentRunORM | None:
        if not self._listing_exists(listing_id):
            return None
        existing = (
            self.db.query(EnrichmentRunORM)
            .filter(
                EnrichmentRunORM.listing_id == listing_id,
                EnrichmentRunORM.enrichment_type == enrichment_type,
            )
            .first()
        )
        now = datetime.now(UTC)
        if existing:
            existing.status = status
            if status == EnrichmentStatus.RUNNING.value:
                existing.started_at = now
            terminal_statuses = (
                EnrichmentStatus.SUCCEEDED.value,
                EnrichmentStatus.FAILED.value,
                EnrichmentStatus.UNAVAILABLE.value,
            )
            if status in terminal_statuses:
                existing.completed_at = now
            existing.error_message = error_message
            self.db.commit()
            self.db.refresh(existing)
            return existing

        run = EnrichmentRunORM(
            listing_id=listing_id,
            enrichment_type=enrichment_type,
            status=status,
            started_at=now if status == EnrichmentStatus.RUNNING.value else None,
            completed_at=now
            if status
            in (EnrichmentStatus.SUCCEEDED.value, EnrichmentStatus.FAILED.value, EnrichmentStatus.UNAVAILABLE.value)
            else None,
            error_message=error_message,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def save_enriched_field(
        self,
        listing_id: UUID,
        field_name: str,
        value: object,
        status: str,
        source: str,
        confidence: str = "MEDIUM",
        provenance: str = "CALCULATED",
        assumptions: list[str] | None = None,
    ) -> None:
        if not self._listing_exists(listing_id):
            return
        existing = (
            self.db.query(EnrichedFieldORM)
            .filter(
                EnrichedFieldORM.listing_id == listing_id,
                EnrichedFieldORM.field_name == field_name,
            )
            .first()
        )
        now = datetime.now(UTC)
        if existing:
            existing.value_json = value
            existing.status = status
            existing.source = source
            existing.retrieved_at = now
            existing.confidence = confidence
            existing.provenance = provenance
            existing.assumptions_json = assumptions or []
        else:
            self.db.add(
                EnrichedFieldORM(
                    listing_id=listing_id,
                    field_name=field_name,
                    value_json=value,
                    status=status,
                    source=source,
                    retrieved_at=now,
                    confidence=confidence,
                    provenance=provenance,
                    assumptions_json=assumptions or [],
                )
            )
        self.db.commit()

    def get_enriched_fields(self, listing_id: UUID) -> list[EnrichedFieldORM]:
        return self.db.query(EnrichedFieldORM).filter(EnrichedFieldORM.listing_id == listing_id).all()

    def get_runs_for_session_listings(self, listing_ids: list[UUID]) -> list[EnrichmentRunORM]:
        if not listing_ids:
            return []
        return (
            self.db.query(EnrichmentRunORM)
            .filter(EnrichmentRunORM.listing_id.in_(listing_ids))
            .all()
        )

    def mark_runs_queued(self, listing_ids: list[UUID]) -> None:
        """Reset prior run rows so status polling represents the new job."""
        runs = self.get_runs_for_session_listings(listing_ids)
        for run in runs:
            run.status = EnrichmentStatus.QUEUED.value
            run.started_at = None
            run.completed_at = None
            run.error_message = None
        if runs:
            self.db.commit()

    def mark_queued_runs_failed(self, listing_ids: list[UUID], message: str) -> None:
        """Avoid leaving a run permanently queued when dispatch itself fails."""
        runs = self.get_runs_for_session_listings(listing_ids)
        for run in runs:
            if run.status == EnrichmentStatus.QUEUED.value:
                run.status = EnrichmentStatus.FAILED.value
                run.completed_at = datetime.now(UTC)
                run.error_message = message
        if runs:
            self.db.commit()

    def save_journey_estimate(
        self,
        listing_id: UUID,
        important_location_id: UUID,
        mode: str,
        requested_day_type: str,
        requested_time_local,
        timezone: str,
        resolved_departure_at: datetime | None,
        duration_seconds: int | None,
        difference_from_fastest_seconds: int | None,
        is_fastest: bool | None,
        status: str,
        provider: str,
        provider_status: str | None,
    ) -> JourneyEstimateORM | None:
        if not self._listing_exists(listing_id):
            return None
        existing = (
            self.db.query(JourneyEstimateORM)
            .filter(
                JourneyEstimateORM.listing_id == listing_id,
                JourneyEstimateORM.important_location_id == important_location_id,
                JourneyEstimateORM.mode == mode,
            )
            .first()
        )
        # `timezone` is the persisted IANA timezone string for the journey.
        # Use the imported UTC singleton for the retrieval timestamp.
        now = datetime.now(UTC)
        if existing:
            existing.duration_seconds = duration_seconds
            existing.difference_from_fastest_seconds = difference_from_fastest_seconds
            existing.is_fastest = is_fastest
            existing.status = status
            existing.provider_status = provider_status
            existing.resolved_departure_at = resolved_departure_at
            existing.retrieved_at = now
            self.db.commit()
            self.db.refresh(existing)
            return existing

        row = JourneyEstimateORM(
            listing_id=listing_id,
            important_location_id=important_location_id,
            mode=mode,
            requested_day_type=requested_day_type,
            requested_time_local=requested_time_local,
            timezone=timezone,
            resolved_departure_at=resolved_departure_at,
            duration_seconds=duration_seconds,
            difference_from_fastest_seconds=difference_from_fastest_seconds,
            is_fastest=is_fastest,
            status=status,
            provider=provider,
            provider_status=provider_status,
            retrieved_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_journey_estimates(self, listing_ids: list[UUID]) -> list[JourneyEstimateORM]:
        if not listing_ids:
            return []
        return (
            self.db.query(JourneyEstimateORM)
            .filter(JourneyEstimateORM.listing_id.in_(listing_ids))
            .all()
        )

    def get_field(self, listing_id: UUID, field_name: str) -> EnrichedFieldORM | None:
        return (
            self.db.query(EnrichedFieldORM)
            .filter(
                EnrichedFieldORM.listing_id == listing_id,
                EnrichedFieldORM.field_name == field_name,
            )
            .first()
        )

    def _listing_exists(self, listing_id: UUID) -> bool:
        return (
            self.db.query(ConfirmedListingORM.id)
            .filter(ConfirmedListingORM.id == listing_id)
            .first()
            is not None
        )
