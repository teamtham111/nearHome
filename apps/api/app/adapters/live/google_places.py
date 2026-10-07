"""Live Google Places adapter."""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from app.adapters.base import AdapterError, PlaceDetails, PlaceSuggestion
from app.adapters.provider_schemas.google_places import (
    GooglePlaceDetailsResponse,
    GooglePlacesAutocompleteResponse,
)
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
            try:
                data = GooglePlacesAutocompleteResponse.model_validate(resp.json())
            except (TypeError, ValueError, ValidationError) as exc:
                raise AdapterError(
                    "Google Places returned malformed autocomplete data.",
                    error_code="invalid_output",
                ) from exc

        suggestions = []
        for item in data.suggestions:
            place_pred = item.place_prediction
            if place_pred is None:
                continue
            main_text = place_pred.structured_format.main_text if place_pred.structured_format else None
            suggestions.append(
                PlaceSuggestion(
                    place_id=place_pred.place_id,
                    description=place_pred.text.text,
                    main_text=main_text.text if main_text else place_pred.text.text,
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
            try:
                data = GooglePlaceDetailsResponse.model_validate(resp.json())
            except (TypeError, ValueError, ValidationError) as exc:
                raise AdapterError(
                    "Google Places returned malformed place details.",
                    error_code="invalid_output",
                ) from exc

        return PlaceDetails(
            place_id=data.place_id,
            formatted_address=data.formatted_address,
            latitude=data.location.latitude,
            longitude=data.location.longitude,
            provider=self.provider,
        )
