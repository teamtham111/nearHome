"""Download a complete official data.gov.sg dataset snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_pipeline.data_gov_client import DataGovClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_id")
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--skip-initiate",
        action="store_true",
        help="Poll directly (required by data.gov.sg for non-CSV datasets such as GeoJSON)",
    )
    parser.add_argument("--expected-suffix", default=None, help="Preferred file suffix if the response is a ZIP")
    args = parser.parse_args()
    result = DataGovClient().download(
        args.dataset_id,
        args.output,
        initiate=not args.skip_initiate,
        expected_suffix=args.expected_suffix,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
