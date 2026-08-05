"""Official data.gov.sg HDB carpark availability provider with short caching."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class AvailabilityRecord:
    carpark_no: str
    lot_type: str
    total_lots: int | None
    available_lots: int | None
    availability_pct: float | None
    observed_at: datetime
    status: str
    source: str
    timestamp_valid: bool = True


class CarparkAvailabilityProvider:
    _cache: tuple[datetime, list[AvailabilityRecord]] | None = None
    _lock = Lock()

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def fetch(self) -> list[AvailabilityRecord]:
        now = datetime.now(UTC)
        with self._lock:
            cache_ttl = timedelta(seconds=settings.hdb_carpark_availability_cache_seconds)
            if self._cache and now - self._cache[0] < cache_ttl:
                return self._cache[1]

        headers = {"X-Api-Key": settings.data_gov_sg_api_key} if settings.data_gov_sg_api_key else {}
        try:
            payload = None
            for attempt in range(2):
                try:
                    if self._client is not None:
                        response = self._client.get(settings.hdb_carpark_availability_url, headers=headers, timeout=15)
                    else:
                        response = httpx.get(settings.hdb_carpark_availability_url, headers=headers, timeout=15)
                    response.raise_for_status()
                    payload = response.json()
                    break
                except httpx.HTTPError:
                    if attempt == 1:
                        raise
            records = _parse_response(payload or {}, now)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise AvailabilityProviderError(f"Official HDB availability is temporarily unavailable: {exc}") from exc

        with self._lock:
            self._cache = (now, records)
        return records

    def by_carpark(self, carpark_no: str) -> list[AvailabilityRecord]:
        try:
            records = self.fetch()
        except AvailabilityProviderError:
            return []
        return [r for r in records if r.carpark_no == carpark_no]

    @classmethod
    def reset_cache(cls) -> None:
        with cls._lock:
            cls._cache = None


class AvailabilityProviderError(RuntimeError):
    pass


def _parse_response(payload: dict, retrieved_at: datetime) -> list[AvailabilityRecord]:
    items = payload.get("items") or []
    if not items:
        return []
    observed_at = _parse_datetime(items[0].get("timestamp"))
    records: list[AvailabilityRecord] = []
    for carpark in items[0].get("carpark_data") or []:
        carpark_no = str(carpark.get("carpark_number") or "").strip()
        if not carpark_no:
            continue
        update_at = _parse_datetime(carpark.get("update_datetime")) or observed_at
        timestamp_valid = update_at is not None
        stored_at = update_at or retrieved_at
        for info in carpark.get("carpark_info") or []:
            lot_type = str(info.get("lot_type") or "").strip().upper()
            if not lot_type:
                continue
            total = _optional_int(info.get("total_lots"))
            available = _optional_int(info.get("lots_available"))
            pct = round(available / total * 100, 1) if total and available is not None and total > 0 else None
            stale = timestamp_valid and retrieved_at - stored_at > timedelta(
                minutes=settings.hdb_carpark_availability_stale_minutes
            )
            records.append(
                AvailabilityRecord(
                    carpark_no=carpark_no,
                    lot_type=lot_type,
                    total_lots=total,
                    available_lots=available,
                    availability_pct=pct,
                    observed_at=stored_at,
                    status="TIMESTAMP_UNAVAILABLE" if not timestamp_valid else "STALE" if stale else "LIVE",
                    source="data.gov.sg HDB Carpark Availability API",
                    timestamp_valid=timestamp_valid,
                )
            )
    return records


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # data.gov.sg's carpark update_datetime is Singapore local time when
        # it has no offset. Treating it as UTC would shift historical buckets
        # and stale detection by eight hours.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Singapore"))
        return parsed.astimezone(UTC)
    except ValueError:
        return None
