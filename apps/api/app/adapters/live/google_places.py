"""Live Google Places adapter."""

from __future__ import annotations

import httpx

from app.adapters.base import PlaceDetails, PlaceSuggestion
from app.core.config import settings


class LiveGooglePlacesAdapter:
    provider = "GOOGLE_PLACES"

    def __init__(self) -> None:
        self.api_key = settings.google_maps_api_key

    def autocomplete(self, query: str) -> list[PlaceSuggestion]:
        url = "https://places.googleapis.com/v1/places:autocomplete"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
        }
        body = {
            "input": query,
            "includedRegionCodes": ["sg"],
        }
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        suggestions = []
        for item in data.get("suggestions", []):
            place_pred = item.get("placePrediction", {})
            suggestions.append(
                PlaceSuggestion(
                    place_id=place_pred.get("placeId", ""),
                    description=place_pred.get("text", {}).get("text", ""),
                    main_text=place_pred.get("structuredFormat", {})
                    .get("mainText", {})
                    .get("text", ""),
                )
            )
        return suggestions

    def get_details(self, place_id: str) -> PlaceDetails:
        url = f"https://places.googleapis.com/v1/places/{place_id}"
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "id,formattedAddress,location",
        }
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return PlaceDetails(
            place_id=data.get("id", place_id),
            formatted_address=data.get("formattedAddress", ""),
            latitude=data["location"]["latitude"],
            longitude=data["location"]["longitude"],
            provider=self.provider,
        )
