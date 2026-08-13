"""Live OneMap geocoding adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.adapters.base import GeocodeResult
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
            self._token = resp.json()["access_token"]
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
    def _search_queries(address: str):
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
        params = {"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1}
        headers = {"Authorization": token}
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        if not results:
            raise ValueError(f"No geocode result for: {query}")

        r = next((row for row in results if self._is_usable_match(row, original_address)), None)
        if r is None:
            raise ValueError(f"OneMap returned no coordinate/address match for: {original_address}")
        return GeocodeResult(
            latitude=float(r["LATITUDE"]),
            longitude=float(r["LONGITUDE"]),
            formatted_address=r.get("ADDRESS", original_address),
            postal_code=r.get("POSTAL"),
            town=r.get("PLANNING_AREA"),
            block=r.get("BLK_NO"),
            street=r.get("ROAD_NAME"),
            provider=self.provider,
            provenance="OFFICIAL",
            retrieved_at=datetime.now(UTC),
        )

    @staticmethod
    def _is_usable_match(result: dict, original_address: str) -> bool:
        """Reject obviously unsafe or wrong-HDB-block provider results.

        OneMap search results are ranked but can contain similarly named
        streets and POIs. A parsed HDB listing therefore must not silently
        accept a result with a conflicting block/street. Missing provider
        address components remain allowed because some legitimate OneMap rows
        do not expose them; their coordinate is still checked independently.
        """
        try:
            latitude = float(result["LATITUDE"])
            longitude = float(result["LONGITUDE"])
        except (KeyError, TypeError, ValueError):
            return False
        if not (
            _SINGAPORE_LATITUDE_RANGE[0] <= latitude <= _SINGAPORE_LATITUDE_RANGE[1]
            and _SINGAPORE_LONGITUDE_RANGE[0] <= longitude <= _SINGAPORE_LONGITUDE_RANGE[1]
        ):
            return False
        requested = canonical_hdb_address_key(original_address)
        returned = canonical_hdb_parts(result.get("BLK_NO"), result.get("ROAD_NAME"))
        return requested is None or returned is None or requested == returned
