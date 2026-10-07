"""Live OneMap geocoding adapter."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.base import GeocodeResult
from app.adapters.provider_schemas.onemap import OneMapSearchResponse, OneMapSearchResult, OneMapTokenResponse
from app.core.config import settings
from app.utils.hdb_address import canonical_hdb_address_key, canonical_hdb_parts

# Broad defensive envelope, not an address-quality score. It prevents a
# provider coordinate/order error from becoming plausible downstream input.
_SINGAPORE_LATITUDE_RANGE = (1.15, 1.49)
_SINGAPORE_LONGITUDE_RANGE = (103.58, 104.12)


class LiveOneMapAdapter:
    provider = "ONEMAP"
    _token: str | None = None

    def _get_token(self) -> str:
        if self._token:
            return self._token
        url = "https://www.onemap.gov.sg/api/auth/post/getToken"
        body = {"email": settings.onemap_email, "password": settings.onemap_password}
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=body)
            resp.raise_for_status()
            try:
                token_response = OneMapTokenResponse.model_validate(resp.json())
            except (TypeError, ValueError, ValidationError) as exc:
                raise ValueError("OneMap returned a malformed authentication response") from exc
            self._token = token_response.access_token
        return self._token

    def geocode(self, address: str) -> GeocodeResult:
        last_error: Exception | None = None
        for query in self._search_queries(address):
            try:
                return self._geocode_query(query, address)
            except ValueError as exc:
                last_error = exc
        raise ValueError(f"No geocode result for: {address}") from last_error

    @staticmethod
    def _search_queries(address: str) -> Iterator[str]:  # normalises the input going to onemap
        cleaned = address.strip()
        yield cleaned
        simplified = cleaned
        for prefix in ("Blk ", "BLK ", "Block ", "BLOCK "):
            if simplified.lower().startswith(prefix.lower()):
                simplified = simplified[len(prefix) :].strip()
                break
        if simplified != cleaned:
            yield simplified
        if "singapore" not in simplified.lower():
            yield f"{simplified}, Singapore"

    def _geocode_query(self, query: str, original_address: str) -> GeocodeResult:
        token = self._get_token()
        url = "https://www.onemap.gov.sg/api/common/elastic/search"
        params: dict[str, str | int] = {
            "searchVal": query,
            "returnGeom": "Y",
            "getAddrDetails": "Y",
            "pageNum": 1,
        }
        headers = {"Authorization": token}
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            try:
                data = OneMapSearchResponse.model_validate(resp.json())
            except (TypeError, ValueError, ValidationError) as exc:
                raise ValueError("OneMap returned a malformed search response") from exc

        results = data.results
        if not results:
            raise ValueError(f"No geocode result for: {query}")

        r = next((row for row in results if self._is_usable_match(row, original_address)), None)
        if r is None:
            raise ValueError(f"OneMap returned no coordinate/address match for: {original_address}")
        return GeocodeResult(
            latitude=r.latitude,
            longitude=r.longitude,
            formatted_address=r.address or original_address,
            postal_code=r.postal_code,
            town=r.planning_area,
            block=r.block,
            street=r.road_name,
            provider=self.provider,
            provenance="OFFICIAL",
            retrieved_at=datetime.now(UTC),
        )

    # converts onemaps schema into nearhomes schema

    @staticmethod
    def _is_usable_match(result: OneMapSearchResult | dict[str, Any], original_address: str) -> bool:
        """Reject obviously unsafe or wrong-HDB-block provider results.

        OneMap search results are ranked but can contain similarly named
        streets and POIs. A parsed HDB listing therefore must not silently
        accept a result with a conflicting block/street. Missing provider
        address components remain allowed because some legitimate OneMap rows
        do not expose them; their coordinate is still checked independently.
        """
        if isinstance(result, dict):
            try:
                result = OneMapSearchResult.model_validate(result)
            except ValidationError:
                return False
        latitude = result.latitude
        longitude = result.longitude
        if not (
            _SINGAPORE_LATITUDE_RANGE[0] <= latitude <= _SINGAPORE_LATITUDE_RANGE[1]
            and _SINGAPORE_LONGITUDE_RANGE[0] <= longitude <= _SINGAPORE_LONGITUDE_RANGE[1]
        ):
            return False
        requested = canonical_hdb_address_key(original_address)
        returned = canonical_hdb_parts(result.block, result.road_name)
        return requested is None or returned is None or requested == returned
