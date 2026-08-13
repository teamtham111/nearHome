"""Filter SLA National Map Line GeoJSON to official ``Layers/Major_Road``.

Download the official SLA National Map Line GeoJSON from data.gov.sg first,
then run this script. The upstream download is large, so it is intentionally
an offline refresh step rather than an enrichment-time dependency.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data_pipeline" / "fixtures" / "sla_major_roads.geojson"
SOURCE_DATASET = "data.gov.sg National Map Line d_10480c0b59e65663dfae1028ff4aa8bb"


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter SLA National Map Line to Major_Road geometries")
    parser.add_argument("input", type=Path, help="Downloaded SLA National Map Line GeoJSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    features = [
        feature
        for feature in payload.get("features", [])
        if feature.get("properties", {}).get("FOLDERPATH") == "Layers/Major_Road"
        and feature.get("geometry", {}).get("type") in {"LineString", "MultiLineString"}
    ]
    if not features:
        raise SystemExit("No SLA Layers/Major_Road LineString features found; input is not the expected dataset.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "source": SOURCE_DATASET,
                "filtered_folderpath": "Layers/Major_Road",
                "retrieved_at": datetime.now(UTC).isoformat(),
                "features": features,
            },
            separators=(",", ":"),
        )
    )
    print(f"Wrote {len(features)} SLA Major_Road features to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
