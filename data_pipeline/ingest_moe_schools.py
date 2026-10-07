"""Normalize and geocode the official MOE school directory snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.provider_schemas.reference import MoeSchoolSnapshotRow

from data_pipeline.data_gov_client import write_json_atomic

DATASET_ID = "d_688b934f82c1059ed0a6993d2a829089"
DEFAULT_OUTPUT = ROOT / "data_pipeline" / "fixtures" / "moe_schools.json"
DEFAULT_META = ROOT / "data_pipeline" / "snapshots" / "moe_schools_meta.json"
_ROWS_ADAPTER = TypeAdapter(list[MoeSchoolSnapshotRow])


def _normalized_row(row: dict[str, object]) -> dict[str, str]:
    return {
        str(key).strip().casefold().replace(" ", "_"): str(value or "").strip()
        for key, value in row.items()
        if key is not None
    }


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [_normalized_row(dict(row)) for row in csv.DictReader(handle)]


def existing_coordinates(path: Path) -> dict[str, tuple[float, float, str]]:
    if not path.is_file():
        return {}
    rows = _ROWS_ADAPTER.validate_json(path.read_text(encoding="utf-8"))
    return {
        row.school_name.strip().casefold(): (row.latitude, row.longitude, row.address.strip().casefold())
        for row in rows
    }


def transform(
    source_rows: list[dict[str, str]],
    geocode: Callable[[str], tuple[float, float]],
    *,
    cached: dict[str, tuple[float, float, str]] | None = None,
    geocode_delay_seconds: float = 0.0,
) -> list[dict[str, object]]:
    cached = cached or {}
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for source in source_rows:
        name = (source.get("school_name") or source.get("school") or "").strip()
        address = (source.get("address") or "").strip()
        postal_code = (source.get("postal_code") or source.get("postal") or "").strip()
        level = (
            source.get("mainlevel_code")
            or source.get("main_level")
            or source.get("school_level")
            or source.get("level")
            or ""
        ).strip()
        if not name or not address or not level:
            raise ValueError(f"MOE source row is missing school_name/address/level: {name or '<unnamed>'}")
        key = name.casefold()
        if key in seen:
            raise ValueError(f"MOE source contains a duplicate school name: {name}")
        seen.add(key)

        prior = cached.get(key)
        if prior and prior[2] == address.casefold():
            latitude, longitude = prior[0], prior[1]
        else:
            queries = ([postal_code] if postal_code else []) + [f"{address}, Singapore"]
            last_error: ValueError | None = None
            for query in queries:
                try:
                    latitude, longitude = geocode(query)
                    break
                except ValueError as exc:
                    last_error = exc
            else:
                raise ValueError(f"OneMap could not geocode MOE school: {name}") from last_error
            if geocode_delay_seconds:
                time.sleep(geocode_delay_seconds)
        result.append(
            {
                "school_name": name,
                "level": level,
                "latitude": latitude,
                "longitude": longitude,
                "address": address,
            }
        )
    return result


def _live_geocoder() -> Callable[[str], tuple[float, float]]:
    from app.adapters.live.onemap import LiveOneMapAdapter
    from app.core.config import settings

    if not settings.onemap_email or not settings.onemap_password:
        raise RuntimeError("ONEMAP_EMAIL and ONEMAP_PASSWORD are required to rebuild the MOE school snapshot")
    adapter = LiveOneMapAdapter()

    def geocode(query: str) -> tuple[float, float]:
        result = adapter.geocode(query)
        return result.latitude, result.longitude

    return geocode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="Official General information of schools CSV")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META)
    parser.add_argument("--geocode-delay", type=float, default=0.1)
    args = parser.parse_args()
    if args.geocode_delay < 0:
        raise SystemExit("--geocode-delay must be non-negative")

    rows = read_rows(args.csv_path)
    normalized = transform(
        rows,
        _live_geocoder(),
        cached=existing_coordinates(args.output),
        geocode_delay_seconds=args.geocode_delay,
    )
    validated = _ROWS_ADAPTER.validate_python(normalized)
    payload = [row.model_dump(mode="json") for row in validated]
    write_json_atomic(args.output, payload)
    checksum = hashlib.sha256(args.output.read_bytes()).hexdigest()
    write_json_atomic(
        args.meta_output,
        {
            "dataset_id": DATASET_ID,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "row_count": len(payload),
            "checksum": checksum,
            "output": str(args.output),
            "coordinate_source": "OneMap",
        },
    )
    print(f"Wrote {len(payload)} geocoded MOE schools to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
