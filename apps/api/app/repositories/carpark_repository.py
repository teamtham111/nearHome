"""Persistence for official HDB carpark data and derived parking evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.adapters.parking.hdb_availability import AvailabilityRecord
from app.adapters.parking.hdb_carpark import HdbCarpark
from app.domain.transport_models import ComponentResult
from app.models.orm import (
    CarparkAvailabilitySnapshotORM,
    HdbCarparkORM,
    ListingCarparkMatchORM,
    ParkingMetricORM,
)


class CarparkRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def import_static_records(self, carparks: list[HdbCarpark]) -> dict[str, int | str]:
        """Idempotently mirror the official fixture in one read/one commit.

        This is an explicit maintenance operation. It is intentionally never
        called from a user enrichment run: scoring reads the versioned fixture
        bundled with the worker, while this table retains auditable parking
        evidence and availability history.
        """
        source_by_number = {carpark.carpark_no: carpark for carpark in carparks if carpark.carpark_no}
        if not source_by_number:
            return {"status": "empty_source", "inserted": 0, "updated": 0, "unchanged": 0}

        existing_by_number = {row.carpark_no: row for row in self.db.query(HdbCarparkORM).all()}
        inserted = updated = unchanged = 0
        for carpark_no, carpark in source_by_number.items():
            values = _static_values(carpark)
            row = existing_by_number.get(carpark_no)
            if row is None:
                self.db.add(HdbCarparkORM(carpark_no=carpark_no, refreshed_at=datetime.now(UTC), **values))
                inserted += 1
                continue
            if all(getattr(row, key) == value for key, value in values.items()):
                unchanged += 1
                continue
            for key, value in values.items():
                setattr(row, key, value)
            row.refreshed_at = datetime.now(UTC)
            updated += 1

        if inserted or updated:
            self.db.commit()
            return {"status": "imported", "inserted": inserted, "updated": updated, "unchanged": unchanged}
        return {"status": "unchanged", "inserted": 0, "updated": 0, "unchanged": unchanged}

    def replace_static_records(self, carparks: list[HdbCarpark]) -> dict[str, int | str]:
        """Backward-compatible name for explicit callers; never use in enrichment."""
        return self.import_static_records(carparks)

    def save_availability(self, records: list[AvailabilityRecord]) -> None:
        for record in records:
            exists = self.db.query(CarparkAvailabilitySnapshotORM).filter(
                CarparkAvailabilitySnapshotORM.carpark_no == record.carpark_no,
                CarparkAvailabilitySnapshotORM.lot_type == record.lot_type,
                CarparkAvailabilitySnapshotORM.observed_at == record.observed_at,
            ).first()
            if exists:
                continue
            self.db.add(
                CarparkAvailabilitySnapshotORM(
                    carpark_no=record.carpark_no,
                    lot_type=record.lot_type,
                    total_lots=record.total_lots,
                    available_lots=record.available_lots,
                    availability_pct=record.availability_pct,
                    status=record.status,
                    observed_at=record.observed_at,
                    source=record.source,
                )
            )
        self.db.commit()

    def historical_summary(self, carpark_no: str, lot_type: str | None = None) -> dict:
        cutoff = datetime.now(UTC) - timedelta(days=90)
        query = self.db.query(CarparkAvailabilitySnapshotORM).filter(
            CarparkAvailabilitySnapshotORM.carpark_no == carpark_no,
            CarparkAvailabilitySnapshotORM.observed_at >= cutoff,
            CarparkAvailabilitySnapshotORM.status == "LIVE",
            CarparkAvailabilitySnapshotORM.availability_pct.is_not(None),
        )
        if lot_type:
            query = query.filter(CarparkAvailabilitySnapshotORM.lot_type == lot_type)
        rows = query.order_by(CarparkAvailabilitySnapshotORM.observed_at).all()
        percentages = [float(row.availability_pct) for row in rows if row.availability_pct is not None]
        if not percentages:
            return {"sample_size": 0, "date_range": None}
        singapore = ZoneInfo("Asia/Singapore")
        local_times = [row.observed_at.astimezone(singapore) for row in rows]
        weekdays = [value for local, value in zip(local_times, percentages, strict=False) if local.weekday() < 5]
        weekday_morning = [
            value
            for local, value in zip(local_times, percentages, strict=False)
            if local.weekday() < 5 and 6 <= local.hour < 11
        ]
        weekday_evening = [
            value
            for local, value in zip(local_times, percentages, strict=False)
            if local.weekday() < 5 and 17 <= local.hour < 22
        ]
        weekend = [value for local, value in zip(local_times, percentages, strict=False) if local.weekday() >= 5]
        return {
            "sample_size": len(percentages),
            "median_availability_pct": _median(percentages),
            "median_weekday_availability_pct": _median(weekdays) if weekdays else None,
            "median_weekday_morning_availability_pct": _median(weekday_morning) if weekday_morning else None,
            "median_weekday_evening_availability_pct": _median(weekday_evening) if weekday_evening else None,
            "median_weekend_availability_pct": _median(weekend) if weekend else None,
            "below_10_pct": round(sum(value < 10 for value in percentages) / len(percentages) * 100, 1),
            "date_range": {
                "from": rows[0].observed_at.isoformat(),
                "to": rows[-1].observed_at.isoformat(),
            },
        }

    def save_matches_and_metric(self, listing_id: UUID, result: ComponentResult, *, commit: bool = True) -> None:
        value = result.value or {}
        self.db.execute(delete(ListingCarparkMatchORM).where(ListingCarparkMatchORM.listing_id == listing_id))
        for match in value.get("candidates", []):
            self.db.add(
                ListingCarparkMatchORM(
                    listing_id=listing_id,
                    carpark_no=match["carpark_no"],
                    rank=match["rank"],
                    haversine_distance_m=match["haversine_distance_m"],
                    routed_walk_distance_m=match.get("walk_distance_metres"),
                    routed_walk_minutes=match.get("walk_minutes"),
                    relevance_score=match.get("relevance_score"),
                    match_type=match.get("match_type", "INFERRED"),
                    confidence=match.get("confidence", "MEDIUM"),
                )
            )
        metric = self.db.query(ParkingMetricORM).filter(ParkingMetricORM.listing_id == listing_id).first()
        if metric:
            metric.score = result.score
            metric.score_status = result.status.value
            metric.metric_json = value
            metric.score_version = "parking-v2"
            metric.calculated_at = datetime.now(UTC)
        else:
            self.db.add(
                ParkingMetricORM(
                    listing_id=listing_id,
                    score=result.score,
                    score_status=result.status.value,
                    metric_json=value,
                    score_version="parking-v2",
                )
            )
        if commit:
            self.db.commit()


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return round(ordered[middle], 1) if len(ordered) % 2 else round((ordered[middle - 1] + ordered[middle]) / 2, 1)


def _static_values(carpark: HdbCarpark) -> dict[str, object]:
    return {
        "address": carpark.address,
        "latitude": carpark.latitude,
        "longitude": carpark.longitude,
        "carpark_type": carpark.carpark_type,
        "source_carpark_type": carpark.source_carpark_type,
        "parking_system_type": carpark.parking_system_type,
        "short_term_parking": carpark.short_term_parking,
        "free_parking": carpark.free_parking,
        "night_parking": carpark.night_parking,
        "carpark_decks": carpark.carpark_decks,
        "gantry_height_m": carpark.gantry_height_m,
        "basement_indicator": carpark.basement_indicator,
        "source": carpark.source,
        "source_updated_at": carpark.source_updated_at,
    }
