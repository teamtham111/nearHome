"""Google Places autocomplete and details response schemas."""

from __future__ import annotations

from pydantic import Field

from app.adapters.provider_schemas.common import ProviderPayload


class GooglePlacesText(ProviderPayload):
    text: str = Field(min_length=1)


class GooglePlacesStructuredFormat(ProviderPayload):
    main_text: GooglePlacesText | None = Field(default=None, alias="mainText")


class GooglePlacePrediction(ProviderPayload):
    place_id: str = Field(alias="placeId", min_length=1)
    text: GooglePlacesText
    structured_format: GooglePlacesStructuredFormat | None = Field(default=None, alias="structuredFormat")


class GooglePlacesSuggestion(ProviderPayload):
    # Google may add non-place suggestion variants. NearHome ignores those,
    # while a present placePrediction must still satisfy its full contract.
    place_prediction: GooglePlacePrediction | None = Field(default=None, alias="placePrediction")


class GooglePlacesAutocompleteResponse(ProviderPayload):
    suggestions: list[GooglePlacesSuggestion] = Field(default_factory=list)


class GooglePlacesLocation(ProviderPayload):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class GooglePlaceDetailsResponse(ProviderPayload):
    place_id: str = Field(alias="id", min_length=1)
    formatted_address: str = Field(alias="formattedAddress", min_length=1)
    location: GooglePlacesLocation
