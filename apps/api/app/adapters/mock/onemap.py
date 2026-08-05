"""Mock OneMap geocoding — clearly labelled demo data."""

from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.base import GeocodeResult

# Demo coordinates keyed by normalised address fragments
_DEMO_LOCATIONS: dict[str, dict] = {
    "BISHAN": {"lat": 1.3521, "lng": 103.8498, "town": "BISHAN", "postal": "570123"},
    "TAMPINES": {"lat": 1.3496, "lng": 103.9568, "town": "TAMPINES", "postal": "520201"},
    "JURONG": {"lat": 1.3330, "lng": 103.7424, "town": "JURONG WEST", "postal": "640401"},
    "PUNGGOL": {"lat": 1.4052, "lng": 103.9025, "town": "PUNGGOL", "postal": "820101"},
}


class MockOneMapAdapter:
    provider = "MOCK_ONEMAP"

    def geocode(self, address: str) -> GeocodeResult:
        upper = address.upper()
        loc = _DEMO_LOCATIONS["BISHAN"]
        for key, data in _DEMO_LOCATIONS.items():
            if key in upper:
                loc = data
                break

        return GeocodeResult(
            latitude=loc["lat"],
            longitude=loc["lng"],
            formatted_address=address.strip(),
            postal_code=loc["postal"],
            town=loc["town"],
            block=None,
            street=None,
            provider=self.provider,
            provenance="MOCK_DEMO_DATA",
            retrieved_at=datetime.now(UTC),
        )
