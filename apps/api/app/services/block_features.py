"""Calculate block-level location features once and reuse them across units.

Every unit in an HDB block shares the building's location, so the Public
Transport and Driving calculations are made once per block
from one consistent starting point and stored with their calculation version
and a fingerprint of the reference data they used. Listing enrichment and the
national backfill both go through ``BlockFeatureService.get_or_compute``.

Unit- or buyer-specific calculations (fair price, lease, storey, named
schools, personal journeys) are not stored here.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.adapters.reference_data import FIXTURES
from app.core.config import settings
from app.core.logging import get_logger
from app.engines.driving.parking_convenience import PARKING_SCORE_VERSION
from app.engines.location_benchmarks.measurements import extract_measurements
from app.engines.transport_config import DRIVING_CONFIG, PT_CONFIG
from app.repositories.block_repository import INCOMPLETE_STATUS, BlockRepository
from app.utils.hdb_block_identity import block_key_from_address, block_key_from_parts

logger = get_logger(__name__)

# Free, nationwide block measurements (benchmarked) and paid, on-demand
# evidence for shortlisted homes (never benchmarked, never backfilled).
FREE_FEATURE_SETS = ("public_transport", "driving_baseline")
PAID_FEATURE_SETS = ("driving_google_evidence",)
FEATURE_SETS = FREE_FEATURE_SETS + PAID_FEATURE_SETS

# Provider and time assumptions are part of every calculation version, so a
# result computed with a different provider or departure assumption is never
# reused or compared with the current one. Bump the code version when engine
# logic changes in a way the configuration hash would not capture.
_FEATURE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "public_transport": {
        "code": "pt-5",
        "provider": "onemap",
        "time_assumption": "walks untimed; feeder transit next weekday at feeder_departure_hour Asia/Singapore",
    },
    "driving_baseline": {
        "code": "drive-baseline-4",
        "provider": "onemap",
        "time_assumption": "baseline static driving and walking time; not traffic-aware",
    },
    "driving_google_evidence": {
        "code": "drive-google-1",
        "provider": "google",
        "time_assumption": "traffic-aware weekday am_peak_hour and off_peak_hour Asia/Singapore",
    },
}

_SOURCE_FILES: dict[str, tuple[str, ...]] = {
    "public_transport": (
        "lta_bus_stops.json",
        "lta_bus_routes.json",
        "lta_bus_services.json",
        "rail/rail_stations.json",
        "rail/rail_edges.csv",
        "rail/rail_lines_structure.csv",
    ),
    "driving_baseline": ("sla_major_roads.geojson", "sla_osm_major_road_mapping.json", "hdb_carparks.json"),
    "driving_google_evidence": ("sla_major_roads.geojson", "sla_osm_major_road_mapping.json"),
}


def _short_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]


def calculation_version(feature_set: str) -> str:
    """Code version, provider, time assumption and configuration hash."""
    if feature_set not in _FEATURE_DEFINITIONS:
        raise ValueError(f"Unknown feature set {feature_set}")
    definition = _FEATURE_DEFINITIONS[feature_set]
    if feature_set == "public_transport":
        config: object = asdict(PT_CONFIG)
    elif feature_set == "driving_baseline":
        config = {"driving": asdict(DRIVING_CONFIG), "parking": PARKING_SCORE_VERSION}
    else:
        config = asdict(DRIVING_CONFIG)
    return f"{definition['code']}-{definition['provider']}-{_short_hash({'config': config, **definition})}"


def feature_definition(feature_set: str) -> dict[str, Any]:
    return dict(_FEATURE_DEFINITIONS[feature_set])


@lru_cache(maxsize=64)
def _file_signature(path: str, mtime_ns: int, size: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def source_manifest(feature_set: str) -> dict[str, Any]:
    """Hash and modification time of every reference file a feature set reads."""
    files: dict[str, Any] = {}
    for name in _SOURCE_FILES[feature_set]:
        path = Path(FIXTURES) / name
        if not path.exists():
            files[name] = None
            continue
        stat = path.stat()
        files[name] = {
            "sha256": _file_signature(str(path), stat.st_mtime_ns, stat.st_size),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }
    return {
        "mode": "demo" if settings.demo_mode else "live",
        "provider": _FEATURE_DEFINITIONS[feature_set]["provider"],
        "files": files,
    }


def source_fingerprint(feature_set: str) -> str:
    manifest = source_manifest(feature_set)
    signature = {"mode": manifest["mode"], "files": {k: (v or {}).get("sha256") for k, v in manifest["files"].items()}}
    return f"{manifest['mode']}-{_short_hash(signature)}"


def _max_age(feature_set: str) -> timedelta | None:
    if feature_set == "public_transport":
        return timedelta(days=settings.block_feature_max_age_days_public_transport)
    if feature_set == "driving_google_evidence":
        return timedelta(days=settings.block_feature_max_age_days_driving)
    if feature_set == "driving_baseline":
        return timedelta(days=settings.block_feature_max_age_days_driving_baseline)
    return None


def compress_payload(payload: dict[str, Any]) -> bytes:
    return gzip.compress(json.dumps(payload, default=str).encode("utf-8"))


def decompress_payload(blob: bytes | None) -> dict[str, Any] | None:
    if not blob:
        return None
    return json.loads(gzip.decompress(blob).decode("utf-8"))


@dataclass(frozen=True)
class BlockIdentity:
    block_key: str
    in_registry: bool
    resolution: str
    town_code: str | None
    town_name: str | None
    postal_code: str | None
    origin_latitude: float | None
    origin_longitude: float | None
    origin_source: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_block(
    repo: BlockRepository,
    *,
    address: str,
    geocode_postal_code: str | None = None,
    geocode_block: str | None = None,
    geocode_street: str | None = None,
    geocode_latitude: float | None = None,
    geocode_longitude: float | None = None,
) -> BlockIdentity | None:
    """Associate a listing with its HDB block.

    Postal code is the most reliable building identifier; block number plus
    normalised street is the fallback because a block number alone is not
    unique in Singapore. When the block is in the registry, its registry
    coordinate is the shared starting point for every unit.
    """
    candidates: list[tuple[str, Any]] = []
    if geocode_postal_code:
        matches = [row for row in repo.blocks_by_postal_code(geocode_postal_code) if row.residential]
        if len(matches) == 1:
            candidates.append(("postal_code", matches[0]))
    for resolution, key in (
        ("listing_address", block_key_from_address(address)),
        ("geocoder_block_street", block_key_from_parts(geocode_block, geocode_street)),
    ):
        if key:
            row = repo.get_block(key)
            if row is not None:
                candidates.append((resolution, row))
    if candidates:
        resolution, row = candidates[0]
        has_registry_origin = row.latitude is not None and row.longitude is not None
        return BlockIdentity(
            block_key=row.block_key,
            in_registry=True,
            resolution=resolution,
            town_code=row.town_code,
            town_name=row.town_name,
            postal_code=row.postal_code or geocode_postal_code,
            origin_latitude=row.latitude if has_registry_origin else geocode_latitude,
            origin_longitude=row.longitude if has_registry_origin else geocode_longitude,
            origin_source="hdb_registry_onemap" if has_registry_origin else "listing_geocode",
        )
    key = block_key_from_address(address) or block_key_from_parts(geocode_block, geocode_street)
    if key is None:
        return None
    return BlockIdentity(
        block_key=key,
        in_registry=False,
        resolution="listing_address_unregistered",
        town_code=None,
        town_name=None,
        postal_code=geocode_postal_code,
        origin_latitude=geocode_latitude,
        origin_longitude=geocode_longitude,
        origin_source="listing_geocode" if geocode_latitude is not None else None,
    )


def incomplete_routing_state(feature_set: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Why a Public Transport payload is incomplete, or ``None`` if it is complete.

    Incomplete means Access rests on routing requests that failed after the
    retry, or on walking detours the policy treats as making the block
    incomplete. ``retryable`` is true only if a failed request could succeed
    on a later attempt (a definitive "no route" answer or a deterministic
    detour will not change). A genuine "no access found" result is complete.
    """
    if feature_set != "public_transport":
        return None
    access = next((c for c in payload.get("components") or [] if c.get("name") == "access"), None)
    if access is None or access.get("status") not in ("partially_assessed", "provider_error"):
        return None
    value = access.get("value") or {}
    failed = int(value.get("routing_failed_requests") or 0)
    detours = len(value.get("walking_detours") or [])
    retryable = int(value.get("retryable_failed_requests") or 0)
    if access.get("status") == "provider_error" and not value:
        failed = retryable = 1  # an old-style total failure with no counts: treat as transient
    if not failed and not detours:
        return None
    return {
        "routing_failed_requests": failed,
        "retryable_failed_requests": retryable,
        "walking_detours": detours,
        "retryable": retryable > 0,
    }


def _payload_status(feature_set: str, payload: dict[str, Any]) -> str:
    if incomplete_routing_state(feature_set, payload) is not None:
        return INCOMPLETE_STATUS
    components = payload.get("components") or []
    assessed = [c for c in components if c.get("status") in ("calculated", "estimated") and c.get("score") is not None]
    return "available" if assessed else "unavailable"


def _payload_provenance(feature_set: str, payload: dict[str, Any]) -> str:
    provenances = {
        str(component.get("provenance"))
        for component in payload.get("components") or []
        if component.get("status") in ("calculated", "estimated") and component.get("score") is not None
    }
    if not provenances:
        return "unavailable"
    if "ROUTED_LIVE" in provenances:
        return "ROUTED_LIVE"
    return sorted(provenances)[0]


@dataclass
class BlockFeatureOutcome:
    payload: dict[str, Any]
    reused: bool
    origin_latitude: float
    origin_longitude: float
    calculation_version: str
    source_fingerprint: str
    computed_at: datetime
    # Set when the stored result is incomplete evidence (see incomplete_routing_state)
    # plus the number of attempts made so far.
    incomplete: dict[str, Any] | None = None


class BlockFeatureService:
    def __init__(self, repo: BlockRepository) -> None:
        self.repo = repo

    def cached(
        self, block_key: str, feature_set: str, *, include_incomplete: bool = False
    ) -> BlockFeatureOutcome | None:
        """Return a reusable stored result for this block, or ``None``.

        A stored incomplete result is never reusable: the live listing path
        calculates for itself. ``include_incomplete`` is for the backfill job
        and status reporting, which need to see it.
        """
        version = calculation_version(feature_set)
        row = self.repo.get_feature(block_key, feature_set, version)
        if row is None or row.source_fingerprint != source_fingerprint(feature_set):
            return None
        if row.status == INCOMPLETE_STATUS and not include_incomplete:
            return None
        max_age = _max_age(feature_set)
        computed_at = row.computed_at if row.computed_at.tzinfo else row.computed_at.replace(tzinfo=UTC)
        if max_age is not None and datetime.now(UTC) - computed_at > max_age:
            return None
        payload = decompress_payload(row.payload_gz)
        if payload is None:
            return None
        return BlockFeatureOutcome(
            payload,
            True,
            row.origin_latitude,
            row.origin_longitude,
            version,
            row.source_fingerprint,
            computed_at,
            incomplete=payload.get("benchmark_incomplete") if row.status == INCOMPLETE_STATUS else None,
        )

    def needs_measurement(
        self, block_key: str, feature_set: str, *, max_attempts: int | None = None
    ) -> bool:
        """Should the backfill job (re)measure this block?

        Yes when there is no current result, or the stored one is incomplete
        because of failed routing requests that could succeed later and fewer
        than ``max_attempts`` attempts have been made. A complete result (even
        a genuine "no access found") and a deterministic incomplete one (walking
        detours, definitive no-route) are final until the source data changes.
        """
        limit = settings.block_feature_max_incomplete_attempts if max_attempts is None else max_attempts
        stored = self.cached(block_key, feature_set, include_incomplete=True)
        if stored is None:
            return True
        state = stored.incomplete
        if not state:
            return False
        return bool(state.get("retryable")) and int(state.get("attempts") or 1) < limit

    def get_or_compute(
        self,
        block_key: str,
        feature_set: str,
        origin: tuple[float, float],
        origin_source: str,
        compute: Callable[[float, float], dict[str, Any]],
        *,
        computed_for: str = "listing",
        reuse: bool = True,
        store_incomplete: bool = False,
    ) -> BlockFeatureOutcome:
        """Reuse the stored result or calculate it.

        ``store_incomplete`` is for the backfill job only: it keeps a result
        that rests on failed routing requests as incomplete evidence (with an
        attempt count) so the next run can retry it. The live listing path
        leaves it off, so it never stores or reuses an incomplete result.
        """
        if reuse:
            cached = self.cached(block_key, feature_set)
            if cached is not None:
                return cached
        latitude, longitude = origin
        payload = compute(latitude, longitude)
        version = calculation_version(feature_set)
        fingerprint = source_fingerprint(feature_set)
        incomplete = incomplete_routing_state(feature_set, payload)
        if payload.get("incomplete_routing") or (incomplete is not None and not store_incomplete):
            # A live result built on failed routing requests is shown to the
            # user but never stored: the block benchmark must not reuse it.
            return BlockFeatureOutcome(
                payload, False, latitude, longitude, version, fingerprint, datetime.now(UTC), incomplete=incomplete
            )
        if incomplete is not None:
            previous = self.cached(block_key, feature_set, include_incomplete=True)
            attempts = int((previous.incomplete or {}).get("attempts") or 0) if previous else 0
            incomplete = {**incomplete, "attempts": attempts + 1, "last_attempt_at": datetime.now(UTC).isoformat()}
            payload = {**payload, "benchmark_incomplete": incomplete}
        row = self.repo.save_feature(
            block_key=block_key,
            feature_set=feature_set,
            calculation_version=version,
            source_fingerprint=fingerprint,
            status=_payload_status(feature_set, payload),
            origin_latitude=latitude,
            origin_longitude=longitude,
            origin_source=origin_source,
            measurements_json=extract_measurements(feature_set, payload),
            payload_gz=compress_payload(payload),
            provenance=_payload_provenance(feature_set, payload),
            computed_for=computed_for,
        )
        return BlockFeatureOutcome(
            payload, False, latitude, longitude, version, fingerprint, row.computed_at, incomplete=incomplete
        )


def payload_provenance(feature_set: str, payload: dict[str, Any]) -> str:
    return _payload_provenance(feature_set, payload)
