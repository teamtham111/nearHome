"""Optional Redis cache-aside layer for routing-provider calls.

Cloud Run inline execution runs correctly without Redis. When configured, this
cache only avoids redundant provider calls; persisted database evidence remains
the source of truth.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Walking/transit topology rarely changes within a day; driving traffic
# conditions are only representative for the same broad time-of-day bucket.
DEFAULT_TTL_SECONDS = 24 * 60 * 60
TRANSIT_TTL_SECONDS = 6 * 60 * 60
TRAFFIC_AWARE_TTL_SECONDS = 15 * 60


def _round_coord(value: float, precision: int = 4) -> float:
    """~11m grid at the equator (4dp) — fine enough to dedupe repeat requests
    without merging genuinely different stops/access points."""
    return round(value, precision)


def _time_bucket(departure_time: datetime | None) -> str:
    if departure_time is None:
        return "none"
    hour = departure_time.hour
    if 6 <= hour < 9:
        period = "AM_PEAK"
    elif 17 <= hour < 20:
        period = "PM_PEAK"
    else:
        period = "OFF_PEAK"
    return f"{departure_time.weekday() < 5}:{period}"


def build_cache_key(
    provider: str,
    method: str,
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    mode: str,
    departure_time: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    parts = {
        "provider": provider,
        "method": method,
        "origin": [_round_coord(c) for c in origin] if origin else None,
        "destination": [_round_coord(c) for c in destination] if destination else None,
        "mode": mode,
        "time_bucket": _time_bucket(departure_time),
        "extra": extra or {},
    }
    raw = json.dumps(parts, sort_keys=True)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    # The namespace is configurable so a response-schema or scoring-evidence
    # change can be isolated without flushing a shared Redis instance.
    return f"{settings.route_cache_namespace}:{digest}"


class RouteCache:
    """Thin cache-aside wrapper. No-ops safely if Redis is unreachable."""

    def __init__(self) -> None:
        self._client: Any = None
        self._connect_failed = False

    def _get_client(self) -> Any:
        if self._client is not None or self._connect_failed:
            return self._client
        if not settings.redis_url:
            self._connect_failed = True
            return None
        try:
            import redis

            self._client = redis.Redis.from_url(settings.redis_url, socket_timeout=1.5, socket_connect_timeout=1.5)
            self._client.ping()
        except Exception as exc:  # pragma: no cover - degrades gracefully
            # Redis client exception text can contain its URL, including an
            # authenticated provider password. Preserve a useful category only.
            logger.warning("route_cache_unavailable", error_category="redis", error_type=type(exc).__name__)
            self._connect_failed = True
            self._client = None
        return self._client

    def get(self, key: str) -> dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:  # pragma: no cover
            logger.warning("route_cache_read_failed", error_category="redis", error_type=type(exc).__name__)
            return None

    def set(self, key: str, value: dict[str, Any], ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.set(key, json.dumps(value), ex=ttl_seconds)
        except Exception as exc:  # pragma: no cover
            logger.warning("route_cache_write_failed", error_category="redis", error_type=type(exc).__name__)

    def close(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
        self._client = None
        self._connect_failed = False


_shared_cache = RouteCache()


def get_route_cache() -> RouteCache:
    return _shared_cache
