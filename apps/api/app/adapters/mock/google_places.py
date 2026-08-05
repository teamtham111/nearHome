"""Mock Google Places — demo autocomplete and place details."""

from __future__ import annotations

from app.adapters.base import PlaceDetails, PlaceSuggestion

_DEMO_PLACES = [
    PlaceSuggestion(
        place_id="demo-place-raffles",
        description="Raffles Place, Singapore",
        main_text="Raffles Place",
    ),
    PlaceSuggestion(
        place_id="demo-place-bishan",
        description="Bishan, Singapore",
        main_text="Bishan",
    ),
    PlaceSuggestion(
        place_id="demo-place-changi",
        description="Changi Airport, Singapore",
        main_text="Changi Airport",
    ),
]

_PLACE_COORDS = {
    "demo-place-raffles": (1.2840, 103.8515, "Raffles Place, Singapore"),
    "demo-place-bishan": (1.3521, 103.8498, "Bishan, Singapore"),
    "demo-place-changi": (1.3644, 103.9915, "Changi Airport, Singapore"),
}


class MockGooglePlacesAdapter:
    provider = "MOCK_GOOGLE_PLACES"

    def autocomplete(self, query: str) -> list[PlaceSuggestion]:
        q = query.lower()
        return [p for p in _DEMO_PLACES if q in p.description.lower() or q in p.main_text.lower()]

    def get_details(self, place_id: str) -> PlaceDetails:
        lat, lng, addr = _PLACE_COORDS.get(
            place_id, (1.3521, 103.8498, "Demo Location, Singapore")
        )
        return PlaceDetails(
            place_id=place_id,
            formatted_address=addr,
            latitude=lat,
            longitude=lng,
            provider=self.provider,
        )
