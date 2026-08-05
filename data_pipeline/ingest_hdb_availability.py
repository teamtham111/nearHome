"""Collect one official HDB carpark-availability snapshot.

Run this from a scheduler (for example, every 5–15 minutes). User-facing
enrichment reads the short-lived provider cache for display, but historical
rows are written only by this separate collector.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.adapters.parking.hdb_availability import CarparkAvailabilityProvider  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.repositories.carpark_repository import CarparkRepository  # noqa: E402


def main() -> int:
    provider = CarparkAvailabilityProvider()
    records = provider.fetch()
    with SessionLocal() as db:
        CarparkRepository(db).save_availability(records)
    print(f"Stored {len(records)} HDB availability records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
