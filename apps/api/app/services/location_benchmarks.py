"""Build, publish and read Singapore and town location benchmarks.

A benchmark version is built from stored block measurements only (no provider
calls), so ranking-logic changes reuse unchanged measurements. Every result
in a version is computed from one consistent set of measurements and is
published by flipping that version to ``published`` in a single transaction,
so a listing view never mixes rankings from different refreshes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.engines.location_benchmarks.comparison import (
    MIN_COVERAGE,
    NATIONAL_MIN_GROUP_SIZE,
    RANKING_VERSION,
    TOWN_MIN_GROUP_SIZE,
    Cohort,
    badness,
    compare,
)
from app.engines.location_benchmarks.measurements import (
    MEASUREMENT_VERSION,
    MEASUREMENTS,
    MEASUREMENTS_BY_KEY,
    UNRANKED_FEATURES,
    extract_measurements,
)
from app.models.orm import BlockLocationFeatureORM
from app.repositories.block_repository import BlockRepository
from app.services.block_features import (
    FEATURE_SETS,
    calculation_version,
    decompress_payload,
    source_fingerprint,
    source_manifest,
)

logger = get_logger(__name__)

SUPPRESSION_RULE = {
    "national_min_group_size": NATIONAL_MIN_GROUP_SIZE,
    "town_min_group_size": TOWN_MIN_GROUP_SIZE,
    "min_coverage": MIN_COVERAGE,
    "description": (
        "A comparison is withheld when its group has fewer valid comparison blocks than the minimum, "
        "or when fewer than the minimum share of the group's HDB residential blocks have a valid measurement."
    ),
}


class BenchmarkPublishError(RuntimeError):
    pass


def _measurements_for_row(db: Session, feature_set: str, row: Any) -> dict[str, dict[str, Any]]:
    """Stored measurements, re-extracted from the saved payload if extraction changed."""
    measurements = dict(row.measurements_json or {})
    expected = {d.key for d in MEASUREMENTS if d.feature_set == feature_set}
    if expected <= set(measurements):
        return measurements
    blob = (
        db.query(BlockLocationFeatureORM.payload_gz)
        .filter(
            BlockLocationFeatureORM.block_key == row.block_key,
            BlockLocationFeatureORM.feature_set == feature_set,
            BlockLocationFeatureORM.calculation_version == calculation_version(feature_set),
        )
        .scalar()
    )
    payload = decompress_payload(blob)
    return extract_measurements(feature_set, payload) if payload else measurements


def build_and_publish(
    db: Session,
    *,
    feature_sets: tuple[str, ...] = FEATURE_SETS,
    allow_demo: bool = False,
    publish: bool = True,
) -> dict[str, Any]:
    """Build a complete benchmark version from stored measurements and publish it."""
    repo = BlockRepository(db)
    registry = repo.residential_blocks()
    if not registry:
        raise BenchmarkPublishError("The HDB block registry is empty; run data_pipeline/ingest_hdb_blocks.py first.")
    town_of = {block.block_key: block.town_code for block in registry}
    town_names = {block.town_code: block.town_name for block in registry if block.town_code}
    national_reference = len(registry)
    town_reference: dict[str, int] = defaultdict(int)
    for block in registry:
        if block.town_code:
            town_reference[block.town_code] += 1

    sources: dict[str, Any] = {}
    # measurement_key -> block_key -> (value, status, measured_at)
    values: dict[str, dict[str, tuple[float | None, str, datetime | None]]] = defaultdict(dict)
    for feature_set in feature_sets:
        version = calculation_version(feature_set)
        fingerprint = source_fingerprint(feature_set)
        if fingerprint.startswith("demo") and not allow_demo:
            raise BenchmarkPublishError("Refusing to publish benchmarks from demo-mode data.")
        sources[feature_set] = {
            "calculation_version": version,
            "source_fingerprint": fingerprint,
            "source_manifest": source_manifest(feature_set),
        }
        used = stale = 0
        for row in repo.features(feature_set, version):
            if row.block_key not in town_of:
                continue  # not an HDB residential reference block
            if row.source_fingerprint != fingerprint:
                stale += 1  # measured from older reference data; refresh first
                continue
            used += 1
            for key, measured in _measurements_for_row(db, feature_set, row).items():
                if key in MEASUREMENTS_BY_KEY:
                    values[key][row.block_key] = (
                        measured.get("value"),
                        measured.get("status", "unavailable"),
                        row.computed_at,
                    )
        sources[feature_set].update({"blocks_used": used, "blocks_stale_excluded": stale})

    meta = repo.registry_metadata()
    version_row = repo.create_version(
        ranking_version=RANKING_VERSION,
        measurement_version=MEASUREMENT_VERSION,
        calculation_versions=sources,
        registry_version=meta["registry_version"],
        registry_source_updated_at=meta["source_updated_at"],
        summary_json={},
    )

    summary: dict[str, Any] = {
        "national_reference_blocks": national_reference,
        "town_reference_blocks": dict(town_reference),
        "town_names": town_names,
        "suppression_rule": SUPPRESSION_RULE,
        "measurements": {},
    }
    result_rows: list[dict[str, Any]] = []
    cohort_rows: list[dict[str, Any]] = []
    for definition in MEASUREMENTS:
        if definition.feature_set not in feature_sets:
            continue
        block_values = values.get(definition.key, {})
        bad = {
            key: badness(value, status, definition.lower_is_better)
            for key, (value, status, _at) in block_values.items()
        }
        national = Cohort.build([b for b in bad.values() if b is not None], national_reference)
        by_town: dict[str, list[float]] = defaultdict(list)
        for key, b in bad.items():
            if b is not None and town_of.get(key):
                by_town[town_of[key]].append(b)
        towns = {code: Cohort.build(by_town.get(code, []), count) for code, count in town_reference.items()}
        cohort_rows.append(
            {
                "version_id": version_row.id,
                "measurement_key": definition.key,
                "scope": "national",
                "cohort_json": national.to_json(),
            }
        )
        cohort_rows.extend(
            {
                "version_id": version_row.id,
                "measurement_key": definition.key,
                "scope": code,
                "cohort_json": cohort.to_json(),
            }
            for code, cohort in towns.items()
        )
        available = 0
        for key, (value, status, measured_at) in block_values.items():
            town_code = town_of.get(key)
            national_result = compare(
                bad[key], national, subject_in_cohort=True, min_group_size=NATIONAL_MIN_GROUP_SIZE
            )
            town_result = (
                compare(bad[key], towns[town_code], subject_in_cohort=True, min_group_size=TOWN_MIN_GROUP_SIZE)
                if town_code in towns
                else None
            )
            available += national_result.status == "available"
            result_rows.append(
                {
                    "version_id": version_row.id,
                    "block_key": key,
                    "measurement_key": definition.key,
                    "value": value,
                    "value_status": status,
                    "town_code": town_code,
                    "national_json": national_result.to_dict(),
                    "town_json": town_result.to_dict()
                    if town_result
                    else {"status": "unavailable", "reason": "No HDB town."},
                    "measured_at": measured_at,
                }
            )
        summary["measurements"][definition.key] = {
            **definition.describe(),
            "valid_blocks": national.valid_count,
            "worst_result_blocks": sum(1 for b in bad.values() if b == float("inf")),
            "unavailable_blocks": sum(1 for b in bad.values() if b is None),
            "not_measured_blocks": national_reference - len(block_values),
            "national_coverage": round(national.coverage, 4),
            "national_comparisons_available": available,
            "towns_meeting_rule": sorted(
                code
                for code, cohort in towns.items()
                if cohort.valid_count - 1 >= TOWN_MIN_GROUP_SIZE and cohort.coverage >= MIN_COVERAGE
            ),
            "complete_national_benchmark": national.coverage >= 0.999,
        }
    summary["unranked_features"] = list(UNRANKED_FEATURES)
    version_row.summary_json = summary
    repo.add_results(result_rows)
    repo.add_cohorts(cohort_rows)
    if publish:
        repo.publish_version(version_row.id)
    db.commit()
    return {"version_id": str(version_row.id), "published": publish, "results": len(result_rows), "summary": summary}


def _with_town_name(result: dict[str, Any], town_name: str | None) -> dict[str, Any]:
    return {**result, "town_name": town_name}


def benchmarks_for_block(
    db: Session,
    block_key: str | None,
    *,
    town_code: str | None = None,
    town_name: str | None = None,
) -> dict[str, Any]:
    """Read saved comparisons for one block from the published version."""
    repo = BlockRepository(db)
    version = repo.published_version()
    base: dict[str, Any] = {"block_key": block_key, "unranked_features": list(UNRANKED_FEATURES)}
    if version is None:
        return {**base, "status": "no_published_version", "measurements": []}
    summary = version.summary_json or {}
    base.update(
        {
            "version_id": str(version.id),
            "published_at": version.published_at.isoformat() if version.published_at else None,
            "ranking_version": version.ranking_version,
            "registry_source_updated_at": version.registry_source_updated_at,
            "national_reference_blocks": summary.get("national_reference_blocks"),
            "suppression_rule": summary.get("suppression_rule"),
        }
    )
    if not block_key:
        return {**base, "status": "block_unknown", "measurements": []}
    rows = {row.measurement_key: row for row in repo.results_for_block(version.id, block_key)}
    block = repo.get_block(block_key)
    town_code = (block.town_code if block else None) or town_code
    town_name = (block.town_name if block else None) or town_name or (summary.get("town_names") or {}).get(town_code)
    measurements: list[dict[str, Any]] = []
    for definition in MEASUREMENTS:
        measure_summary = (summary.get("measurements") or {}).get(definition.key)
        if measure_summary is None:
            continue  # feature set not part of this version
        row = rows.get(definition.key)
        if row is not None:
            entry = {
                "value": row.value,
                "value_status": row.value_status,
                "national": row.national_json,
                "town": _with_town_name(row.town_json, town_name),
                "measured_at": row.measured_at.isoformat() if row.measured_at else None,
                "source": "published_block_result",
            }
        else:
            entry = _compare_unpublished_block(db, repo, version.id, definition, block_key, town_code, town_name)
            if entry is None:
                continue
        measurements.append(
            {
                **definition.describe(),
                **entry,
                "national_coverage": measure_summary.get("national_coverage"),
                "complete_national_benchmark": measure_summary.get("complete_national_benchmark", False),
            }
        )
    return {**base, "status": "available" if measurements else "block_not_measured", "measurements": measurements}


def _compare_unpublished_block(
    db: Session,
    repo: BlockRepository,
    version_id: UUID,
    definition: Any,
    block_key: str,
    town_code: str | None,
    town_name: str | None,
) -> dict[str, Any] | None:
    """Compare a block outside the published results against the stored groups.

    Used for listings whose block is not in the HDB reference registry or was
    measured after the version was built. The block is not a member of the
    stored group, so nothing needs excluding.
    """
    feature = repo.get_feature(block_key, definition.feature_set, calculation_version(definition.feature_set))
    if feature is None:
        return None
    measured = _measurements_for_row(db, definition.feature_set, feature).get(definition.key)
    if not measured:
        return None
    b = badness(measured.get("value"), measured.get("status", "unavailable"), definition.lower_is_better)
    national_cohort = repo.cohort(version_id, definition.key, "national")
    national = (
        compare(
            b,
            Cohort.from_json(national_cohort.cohort_json),
            subject_in_cohort=False,
            min_group_size=NATIONAL_MIN_GROUP_SIZE,
        ).to_dict()
        if national_cohort
        else {"status": "unavailable", "reason": "No published Singapore group."}
    )
    town_cohort = repo.cohort(version_id, definition.key, town_code) if town_code else None
    town = (
        compare(
            b, Cohort.from_json(town_cohort.cohort_json), subject_in_cohort=False, min_group_size=TOWN_MIN_GROUP_SIZE
        ).to_dict()
        if town_cohort
        else {"status": "unavailable", "reason": "This block's HDB town is not known."}
    )
    computed_at = feature.computed_at
    return {
        "value": measured.get("value"),
        "value_status": measured.get("status", "unavailable"),
        "national": national,
        "town": _with_town_name(town, town_name),
        "measured_at": computed_at.isoformat() if isinstance(computed_at, datetime) else None,
        "source": "compared_against_published_groups",
    }


def published_summary(db: Session) -> dict[str, Any] | None:
    version = BlockRepository(db).published_version()
    if version is None:
        return None
    return {
        "version_id": str(version.id),
        "published_at": version.published_at.isoformat() if version.published_at else None,
        "calculation_versions": version.calculation_versions,
        "summary": version.summary_json,
        "checked_at": datetime.now(UTC).isoformat(),
    }
