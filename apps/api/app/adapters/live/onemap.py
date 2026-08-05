"""Live OneMap geocoding adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.adapters.base import GeocodeResult
from app.core.config import settings


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

        r = results[0]
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
