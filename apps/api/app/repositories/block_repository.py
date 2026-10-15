"""Persistence for the HDB block registry, shared block features and benchmarks."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.orm import (
    BlockLocationFeatureORM,
    HdbBlockORM,
    LocationBenchmarkCohortORM,
    LocationBenchmarkResultORM,
    LocationBenchmarkVersionORM,
)

_REGISTRY_FIELDS = (
    "block",
    "street",
    "residential",
    "town_code",
    "town_name",
    "postal_code",
    "latitude",
    "longitude",
    "geocode_status",
    "geocode_match",
    "onemap_road_name",
    "year_completed",
    "max_floor_level",
    "total_dwelling_units",
)


class BlockRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # Registry -----------------------------------------------------------------

    def upsert_registry(self, blocks: Iterable[dict[str, Any]], metadata: dict[str, Any]) -> int:
        existing = {row.block_key: row for row in self.db.scalars(select(HdbBlockORM))}
        count = 0
        for block in blocks:
            row = existing.get(block["block_key"])
            if row is None:
                row = HdbBlockORM(block_key=block["block_key"])
                self.db.add(row)
            for field in _REGISTRY_FIELDS:
                setattr(row, field, block.get(field))
            row.registry_version = metadata.get("registry_version", "unknown")
            row.source_updated_at = metadata.get("source_updated_at")
            row.refreshed_at = datetime.now(UTC)
            count += 1
        return count

    def get_block(self, block_key: str) -> HdbBlockORM | None:
        return self.db.get(HdbBlockORM, block_key)

    def blocks_by_postal_code(self, postal_code: str) -> list[HdbBlockORM]:
        return list(self.db.scalars(select(HdbBlockORM).where(HdbBlockORM.postal_code == postal_code)))

    def residential_blocks(self) -> list[HdbBlockORM]:
        return list(
            self.db.scalars(
                select(HdbBlockORM).where(HdbBlockORM.residential.is_(True)).order_by(HdbBlockORM.block_key)
            )
        )

    def registry_metadata(self) -> dict[str, Any]:
        row = self.db.scalars(select(HdbBlockORM).limit(1)).first()
        if row is None:
            return {"registry_version": None, "source_updated_at": None}
        return {"registry_version": row.registry_version, "source_updated_at": row.source_updated_at}

    # Block features -----------------------------------------------------------

    def get_feature(self, block_key: str, feature_set: str, calculation_version: str) -> BlockLocationFeatureORM | None:
        return self.db.scalars(
            select(BlockLocationFeatureORM).where(
                BlockLocationFeatureORM.block_key == block_key,
                BlockLocationFeatureORM.feature_set == feature_set,
                BlockLocationFeatureORM.calculation_version == calculation_version,
            )
        ).first()

    def save_feature(self, **values: Any) -> BlockLocationFeatureORM:
        row = self.get_feature(values["block_key"], values["feature_set"], values["calculation_version"])
        if row is None:
            row = BlockLocationFeatureORM(**values)
            self.db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        row.computed_at = datetime.now(UTC)
        self.db.flush()
        return row

    def features(self, feature_set: str, calculation_version: str) -> Iterator[BlockLocationFeatureORM]:
        """Stream stored measurements; the compressed payload is not loaded."""
        query = (
            select(
                BlockLocationFeatureORM.block_key,
                BlockLocationFeatureORM.source_fingerprint,
                BlockLocationFeatureORM.measurements_json,
                BlockLocationFeatureORM.computed_at,
            )
            .where(
                BlockLocationFeatureORM.feature_set == feature_set,
                BlockLocationFeatureORM.calculation_version == calculation_version,
            )
            .execution_options(yield_per=1000)
        )
        yield from self.db.execute(query)

    def feature_keys(self, feature_set: str, calculation_version: str, source_fingerprint: str) -> set[str]:
        return set(
            self.db.scalars(
                select(BlockLocationFeatureORM.block_key).where(
                    BlockLocationFeatureORM.feature_set == feature_set,
                    BlockLocationFeatureORM.calculation_version == calculation_version,
                    BlockLocationFeatureORM.source_fingerprint == source_fingerprint,
                )
            )
        )

    # Benchmark versions -------------------------------------------------------

    def create_version(self, **values: Any) -> LocationBenchmarkVersionORM:
        version = LocationBenchmarkVersionORM(status="building", **values)
        self.db.add(version)
        self.db.flush()
        return version

    def add_results(self, rows: list[dict[str, Any]]) -> None:
        self.db.bulk_insert_mappings(LocationBenchmarkResultORM, rows)  # type: ignore[arg-type]

    def add_cohorts(self, rows: list[dict[str, Any]]) -> None:
        self.db.bulk_insert_mappings(LocationBenchmarkCohortORM, rows)  # type: ignore[arg-type]

    def publish_version(self, version_id: UUID) -> None:
        """Atomically make one version the only published version."""
        self.db.execute(
            update(LocationBenchmarkVersionORM)
            .where(LocationBenchmarkVersionORM.status == "published")
            .values(status="superseded")
        )
        self.db.execute(
            update(LocationBenchmarkVersionORM)
            .where(LocationBenchmarkVersionORM.id == version_id)
            .values(status="published", published_at=datetime.now(UTC))
        )

    def published_version(self) -> LocationBenchmarkVersionORM | None:
        return self.db.scalars(
            select(LocationBenchmarkVersionORM)
            .where(LocationBenchmarkVersionORM.status == "published")
            .order_by(LocationBenchmarkVersionORM.published_at.desc())
        ).first()

    def get_version(self, version_id: UUID) -> LocationBenchmarkVersionORM | None:
        return self.db.get(LocationBenchmarkVersionORM, version_id)

    def results_for_block(self, version_id: UUID, block_key: str) -> list[LocationBenchmarkResultORM]:
        return list(
            self.db.scalars(
                select(LocationBenchmarkResultORM).where(
                    LocationBenchmarkResultORM.version_id == version_id,
                    LocationBenchmarkResultORM.block_key == block_key,
                )
            )
        )

    def cohort(self, version_id: UUID, measurement_key: str, scope: str) -> LocationBenchmarkCohortORM | None:
        return self.db.scalars(
            select(LocationBenchmarkCohortORM).where(
                LocationBenchmarkCohortORM.version_id == version_id,
                LocationBenchmarkCohortORM.measurement_key == measurement_key,
                LocationBenchmarkCohortORM.scope == scope,
            )
        ).first()
