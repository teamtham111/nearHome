"""Calculate block-level location features once and reuse them across units.

Every unit in an HDB block shares the building's location, so the Public
Transport, Driving and nearby-school calculations are made once per block
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
from app.engines.schools import SchoolsEngine
from app.engines.transport_config import DRIVING_CONFIG, PT_CONFIG
from app.repositories.block_repository import BlockRepository
from app.utils.hdb_block_identity import block_key_from_address, block_key_from_parts

logger = get_logger(__name__)

FEATURE_SETS = ("public_transport", "driving", "schools")

# Bump the code version when an engine's measurement logic changes in a way
# its configuration hash would not capture.
_ENGINE_CODE_VERSIONS = {"public_transport": "pt-1", "driving": "drive-1", "schools": "schools-1"}

_SOURCE_FILES: dict[str, tuple[str, ...]] = {
    "public_transport": (
        "lta_bus_stops.json",
        "lta_bus_routes.json",
        "lta_bus_services.json",
        "rail/rail_stations.json",
        "rail/rail_edges.csv",
        "rail/rail_lines_structure.csv",
    ),
    "driving": ("sla_osm_major_road_mapping.json", "hdb_carparks.json"),
    "schools": ("moe_schools.json",),
}


def _short_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]


def calculation_version(feature_set: str) -> str:
    """Engine code version plus a hash of the configuration that shapes it."""
    if feature_set == "public_transport":
        config: object = asdict(PT_CONFIG)
    elif feature_set == "driving":
        config = {"driving": asdict(DRIVING_CONFIG), "parking": PARKING_SCORE_VERSION}
    elif feature_set == "schools":
        config = {"search_radius_km": SchoolsEngine.SEARCH_RADIUS_KM}
    else:
        raise ValueError(f"Unknown feature set {feature_set}")
    return f"{_ENGINE_CODE_VERSIONS[feature_set]}-{_short_hash(config)}"


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
    return {"mode": "demo" if settings.demo_mode else "live", "files": files}


def source_fingerprint(feature_set: str) -> str:
    manifest = source_manifest(feature_set)
    signature = {"mode": manifest["mode"], "files": {k: (v or {}).get("sha256") for k, v in manifest["files"].items()}}
    return f"{manifest['mode']}-{_short_hash(signature)}"


def _max_age(feature_set: str) -> timedelta | None:
    if feature_set == "public_transport":
        return timedelta(days=settings.block_feature_max_age_days_public_transport)
    if feature_set == "driving":
        return timedelta(days=settings.block_feature_max_age_days_driving)
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


def _payload_status(feature_set: str, payload: dict[str, Any]) -> str:
    if feature_set == "schools":
        return str(payload.get("score_status") or "unavailable")
    return "available" if payload.get("display_score") is not None else "unavailable"


def _payload_provenance(feature_set: str, payload: dict[str, Any]) -> str:
    if feature_set == "schools":
        return "MOCK_DEMO_DATA" if settings.demo_mode else "OFFICIAL"
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


def schools_block_payload(latitude: float, longitude: float) -> dict[str, Any]:
    """Buyer-independent nearby-school result for a block (no named schools)."""
    result = SchoolsEngine.compute(None, latitude, longitude)  # type: ignore[arg-type]
    return SchoolsEngine.to_dict(result)


class BlockFeatureService:
    def __init__(self, repo: BlockRepository) -> None:
        self.repo = repo

    def cached(self, block_key: str, feature_set: str) -> BlockFeatureOutcome | None:
        """Return a reusable stored result for this block, or ``None``."""
        version = calculation_version(feature_set)
        row = self.repo.get_feature(block_key, feature_set, version)
        if row is None or row.source_fingerprint != source_fingerprint(feature_set):
            return None
        max_age = _max_age(feature_set)
        computed_at = row.computed_at if row.computed_at.tzinfo else row.computed_at.replace(tzinfo=UTC)
        if max_age is not None and datetime.now(UTC) - computed_at > max_age:
            return None
        payload = decompress_payload(row.payload_gz)
        if payload is None:
            return None
        return BlockFeatureOutcome(
            payload, True, row.origin_latitude, row.origin_longitude, version, row.source_fingerprint, computed_at
        )

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
    ) -> BlockFeatureOutcome:
        if reuse:
            cached = self.cached(block_key, feature_set)
            if cached is not None:
                return cached
        latitude, longitude = origin
        payload = compute(latitude, longitude)
        version = calculation_version(feature_set)
        fingerprint = source_fingerprint(feature_set)
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
        return BlockFeatureOutcome(payload, False, latitude, longitude, version, fingerprint, row.computed_at)


def payload_provenance(feature_set: str, payload: dict[str, Any]) -> str:
    return _payload_provenance(feature_set, payload)
