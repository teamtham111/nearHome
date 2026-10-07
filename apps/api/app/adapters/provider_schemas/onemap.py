"""OneMap authentication and search response schemas."""

from __future__ import annotations

from pydantic import Field

from app.adapters.provider_schemas.common import ProviderPayload


class OneMapTokenResponse(ProviderPayload):
    access_token: str = Field(min_length=1)


class OneMapSearchResult(ProviderPayload):
    latitude: float = Field(alias="LATITUDE", allow_inf_nan=False)
    longitude: float = Field(alias="LONGITUDE", allow_inf_nan=False)
    address: str | None = Field(default=None, alias="ADDRESS")
    postal_code: str | None = Field(default=None, alias="POSTAL")
    planning_area: str | None = Field(default=None, alias="PLANNING_AREA")
    block: str | None = Field(default=None, alias="BLK_NO")
    road_name: str | None = Field(default=None, alias="ROAD_NAME")


class OneMapSearchResponse(ProviderPayload):
    results: list[OneMapSearchResult]
