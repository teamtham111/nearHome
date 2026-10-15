"""OneMap (SLA) routing provider for walking, scheduled public transport and baseline driving.

Used for the free, nationwide block measurements: Public Transport Access
(walking and feeder transit), parking walks and baseline major-road access.

Response handling (verified against a live probe on 2026-10-16):

- walk/drive ``route_summary.total_time`` is seconds and ``total_distance``
  metres; ``route_geometry`` is an encoded polyline.
- public transport itinerary ``duration``/``walkTime``/``transitTime``/
  ``waitingTime`` and leg ``duration`` are seconds; ``startTime``/``endTime``
  are milliseconds since the epoch. Requests use Asia/Singapore dates/times.
- LTA service numbers are matched on ``routeShortName`` (``"158"``), never
  the operator-prefixed ``route`` (``"SBST BUS 158"``).
- Transit legs are timetable-based (``realTime`` false); results are marked
  ``schedule_based`` and never described as live predictions.
- The itinerary duration runs from its own start to end. Waiting before the
  itinerary starts and OneMap's ``waitingTime`` are excluded, because Access
  adds its own frequency-based scheduled wait.

Unsupported capabilities (driving alternatives, traffic-aware or time-
dependent driving, route matrices) raise ``RoutingUnavailableError`` so the
caller reports them as unavailable. This provider never falls back to a paid
provider.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.routing.base import (
    RouteMatrixResponse,
    RouteMode,
    RouteResult,
    RouteStep,
    RoutingProvider,
    RoutingProviderError,
    RoutingUnavailableError,
)
from app.adapters.routing.cache import DEFAULT_TTL_SECONDS, TRANSIT_TTL_SECONDS, build_cache_key, get_route_cache
from app.core.config import settings
from app.core.logging import get_logger
from app.engines.time_utils import SINGAPORE_TZ

logger = get_logger(__name__)

ROUTE_URL = "https://www.onemap.gov.sg/api/public/routingsvc/route"
TOKEN_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
PROVIDER_NAME = "ONEMAP_ROUTING"


class _Throttle:
    """Process-wide minimum interval between OneMap requests (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self, interval: float) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next_allowed - now
            self._next_allowed = max(now, self._next_allowed) + interval
        if delay > 0:
            time.sleep(delay)


_THROTTLE = _Throttle()
_TOKEN_LOCK = threading.Lock()
_TOKEN: dict[str, Any] = {"value": None}


def _seconds_to_minutes(seconds: Any) -> float | None:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
        return None
    return round(float(seconds) / 60, 1)


def _epoch_ms_to_singapore(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).astimezone(SINGAPORE_TZ)


def _instruction_step(item: list[Any], mode: str) -> RouteStep:
    """``[turn, road, metres, "lat,lng", seconds, "49m", ..., text]`` → step."""
    distance = item[2] if len(item) > 2 and isinstance(item[2], (int, float)) else 0
    seconds = item[4] if len(item) > 4 else None
    text = item[9] if len(item) > 9 and isinstance(item[9], str) else " ".join(str(p) for p in item[:2] if p)
    return RouteStep(
        instruction=text,
        mode=mode,
        distance_metres=int(round(distance)),
        duration_minutes=_seconds_to_minutes(seconds) or 0.0,
    )


def parse_walk_or_drive(payload: dict[str, Any], mode: str, retrieved_at: datetime) -> RouteResult:
    """Parse a walk/drive response; raise ``RoutingUnavailableError`` if unusable."""
    summary = payload.get("route_summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict) or payload.get("status") not in (0, None):
        raise RoutingUnavailableError(
            f"OneMap returned no {mode.lower()} route: {payload.get('status_message') or payload.get('error')}",
            PROVIDER_NAME,
        )
    minutes = _seconds_to_minutes(summary.get("total_time"))
    distance = summary.get("total_distance")
    if minutes is None or isinstance(distance, bool) or not isinstance(distance, (int, float)):
        raise RoutingUnavailableError("OneMap route omitted a valid time or distance", PROVIDER_NAME)
    steps = [
        _instruction_step(item, mode) for item in payload.get("route_instructions") or [] if isinstance(item, list)
    ]
    return RouteResult(
        duration_minutes=minutes,
        distance_metres=int(round(distance)),
        transfers=None,
        walking_minutes=minutes if mode == "WALK" else None,
        route_steps=steps,
        provider=PROVIDER_NAME,
        departure_time=None,
        arrival_time=None,
        traffic_aware=False,
        warnings=["OneMap driving time is a baseline estimate, not traffic-aware."] if mode == "DRIVE" else [],
        encoded_polyline=payload.get("route_geometry") or None,
        retrieved_at=retrieved_at,
    )


def _leg_minutes(leg: dict[str, Any]) -> float:
    minutes = _seconds_to_minutes(leg.get("duration"))
    if minutes is not None:
        return minutes
    start, end = leg.get("startTime"), leg.get("endTime")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return round((end - start) / 60000, 1)
    return 0.0


def parse_transit(payload: dict[str, Any], retrieved_at: datetime) -> RouteResult:
    """Parse the fastest OneMap public-transport itinerary."""
    plan = payload.get("plan") if isinstance(payload, dict) else None
    itineraries = plan.get("itineraries") if isinstance(plan, dict) else None
    usable = [
        item
        for item in itineraries or []
        if isinstance(item, dict) and _seconds_to_minutes(item.get("duration")) is not None and item.get("legs")
    ]
    if not usable:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("msg") if isinstance(error, dict) else error
        raise RoutingUnavailableError(f"OneMap returned no public-transport itinerary: {message}", PROVIDER_NAME)
    itinerary = min(usable, key=lambda item: item["duration"])
    steps: list[RouteStep] = []
    walking = transit = 0.0
    for leg in itinerary["legs"]:
        minutes = _leg_minutes(leg)
        geometry = (leg.get("legGeometry") or {}).get("points")
        distance = int(round(leg.get("distance") or 0))
        if leg.get("transitLeg"):
            transit += minutes
            vehicle = str(leg.get("mode") or "TRANSIT").upper()
            short_name = leg.get("routeShortName") or None
            steps.append(
                RouteStep(
                    instruction=f"{vehicle} {short_name or leg.get('routeLongName') or ''}".strip(),
                    mode="TRANSIT",
                    distance_metres=distance,
                    duration_minutes=minutes,
                    # Bus service numbers match LTA ServiceNo; rail keeps its
                    # line name so it is never mistaken for a bus service.
                    transit_line=leg.get("routeLongName") or leg.get("route") or short_name,
                    transit_service_number=short_name,
                    vehicle_type=vehicle,
                    encoded_polyline=geometry,
                )
            )
        else:
            walking += minutes
            steps.append(
                RouteStep(
                    instruction="Walk",
                    mode="WALK",
                    distance_metres=distance,
                    duration_minutes=minutes,
                    encoded_polyline=geometry,
                )
            )
    transfers = itinerary.get("transfers")
    if not isinstance(transfers, int):
        transfers = max(0, sum(1 for step in steps if step.mode == "TRANSIT") - 1)
    walk_minutes = _seconds_to_minutes(itinerary.get("walkTime"))
    transit_minutes = _seconds_to_minutes(itinerary.get("transitTime"))
    return RouteResult(
        duration_minutes=_seconds_to_minutes(itinerary["duration"]) or 0.0,
        distance_metres=int(round(sum(step.distance_metres for step in steps))),
        transfers=transfers,
        walking_minutes=walk_minutes if walk_minutes is not None else round(walking, 1),
        route_steps=steps,
        provider=PROVIDER_NAME,
        departure_time=_epoch_ms_to_singapore(itinerary.get("startTime")),
        arrival_time=_epoch_ms_to_singapore(itinerary.get("endTime")),
        traffic_aware=False,
        warnings=["Scheduled public-transport itinerary; not a live vehicle prediction."],
        transit_minutes=transit_minutes if transit_minutes is not None else round(transit, 1),
        retrieved_at=retrieved_at,
        schedule_based=not any(leg.get("realTime") for leg in itinerary["legs"]),
    )


def _route_from_cache(cached: dict[str, Any]) -> RouteResult:
    data = dict(cached)
    data["route_steps"] = [RouteStep(**step) for step in data.get("route_steps", [])]
    for key in ("departure_time", "arrival_time", "retrieved_at"):
        if data.get(key):
            data[key] = datetime.fromisoformat(data[key])
    return RouteResult(**data)


def _route_to_cache(result: RouteResult) -> dict[str, Any]:
    from dataclasses import asdict

    data = asdict(result)
    for key in ("departure_time", "arrival_time", "retrieved_at"):
        if data.get(key):
            data[key] = data[key].isoformat()
    return data


class OneMapRoutingProvider(RoutingProvider):
    provider_name = PROVIDER_NAME

    def __init__(self, *, client: httpx.Client | None = None, min_interval_seconds: float | None = None) -> None:
        if not settings.onemap_email or not settings.onemap_password:
            raise RoutingProviderError("OneMap credentials are not configured", PROVIDER_NAME)
        self._client = client or httpx.Client(timeout=20.0)
        self._owns_client = client is None
        self._cache = get_route_cache()
        self._interval = (
            settings.onemap_min_request_interval_seconds if min_interval_seconds is None else min_interval_seconds
        )
        self.request_count = 0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _token(self, *, refresh: bool = False) -> str:
        with _TOKEN_LOCK:
            if _TOKEN["value"] and not refresh:
                return str(_TOKEN["value"])
            response = self._client.post(
                TOKEN_URL, json={"email": settings.onemap_email, "password": settings.onemap_password}
            )
            if response.status_code != 200:
                raise RoutingProviderError(
                    "OneMap authentication failed", PROVIDER_NAME, retryable=True, http_status=response.status_code
                )
            _TOKEN["value"] = response.json().get("access_token")
            if not _TOKEN["value"]:
                raise RoutingProviderError("OneMap authentication returned no token", PROVIDER_NAME)
            return str(_TOKEN["value"])

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        refreshed = False
        for attempt in range(5):
            _THROTTLE.wait(self._interval)
            self.request_count += 1
            try:
                response = self._client.get(ROUTE_URL, params=params, headers={"Authorization": self._token()})
            except httpx.HTTPError as exc:
                if attempt == 4:
                    raise RoutingProviderError("OneMap request failed", PROVIDER_NAME, retryable=True) from exc
                time.sleep(min(30.0, 2.0**attempt))
                continue
            if response.status_code == 401 and not refreshed:
                self._token(refresh=True)
                refreshed = True
                continue
            if response.status_code in RETRYABLE_STATUS and attempt < 4:
                retry_after = response.headers.get("retry-after")
                time.sleep(
                    float(retry_after) if retry_after and retry_after.isdigit() else min(60.0, 2.0 ** (attempt + 1))
                )
                continue
            if response.status_code != 200:
                raise RoutingProviderError(
                    f"OneMap routing returned HTTP {response.status_code}",
                    PROVIDER_NAME,
                    retryable=response.status_code in RETRYABLE_STATUS,
                    http_status=response.status_code,
                    response_body=response.text[:300],
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise RoutingProviderError("OneMap returned invalid JSON", PROVIDER_NAME) from exc
            if not isinstance(payload, dict):
                raise RoutingProviderError("OneMap returned an unexpected payload", PROVIDER_NAME)
            return payload
        raise RoutingProviderError("OneMap routing kept failing", PROVIDER_NAME, retryable=True)

    def _cached(self, key: str, fetch, ttl: int) -> RouteResult:
        cached = self._cache.get(key)
        if cached:
            return _route_from_cache(cached)
        result = fetch()
        self._cache.set(key, _route_to_cache(result), ttl_seconds=ttl)
        return result

    @staticmethod
    def _point(coord: tuple[float, float]) -> str:
        return f"{coord[0]:.6f},{coord[1]:.6f}"

    def _walk_or_drive(
        self, origin: tuple[float, float], destination: tuple[float, float], route_type: str
    ) -> RouteResult:
        mode = "WALK" if route_type == "walk" else "DRIVE"
        key = build_cache_key(self.provider_name, route_type, origin, destination, mode)

        def fetch() -> RouteResult:
            payload = self._request(
                {"start": self._point(origin), "end": self._point(destination), "routeType": route_type}
            )
            return parse_walk_or_drive(payload, mode, datetime.now(UTC))

        return self._cached(key, fetch, DEFAULT_TTL_SECONDS)

    def get_walking_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        return self._walk_or_drive(origin, destination, "walk")

    def get_baseline_driving_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        """OneMap's single static driving route: a baseline, never traffic-aware."""
        return self._walk_or_drive(origin, destination, "drive")

    def get_driving_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult:
        if traffic_aware:
            raise RoutingUnavailableError(
                "OneMap does not provide traffic-aware or departure-time driving estimates", PROVIDER_NAME
            )
        return self.get_baseline_driving_route(origin, destination)

    def get_driving_alternatives(
        self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
    ) -> list[RouteResult]:
        raise RoutingUnavailableError(
            "Driving alternatives are taken from Google for shortlisted homes only", PROVIDER_NAME
        )

    def get_transit_route(
        self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
    ) -> RouteResult:
        local = departure_time.astimezone(SINGAPORE_TZ)
        key = build_cache_key(self.provider_name, "transit", origin, destination, "TRANSIT", local)

        def fetch() -> RouteResult:
            payload = self._request(
                {
                    "start": self._point(origin),
                    "end": self._point(destination),
                    "routeType": "pt",
                    "date": local.strftime("%m-%d-%Y"),
                    "time": local.strftime("%H:%M:%S"),
                    "mode": "TRANSIT",
                    "maxWalkDistance": "1000",
                    "numItineraries": "3",
                }
            )
            return parse_transit(payload, datetime.now(UTC))

        return self._cached(key, fetch, TRANSIT_TTL_SECONDS)

    def get_route_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        mode: RouteMode,
        departure_time: datetime | None = None,
    ) -> RouteMatrixResponse:
        raise RoutingUnavailableError("OneMap routing has no matrix endpoint", PROVIDER_NAME)


class UnavailableRoutingProvider(RoutingProvider):
    """Stands in when the free provider is not configured.

    Every call fails as a provider error, so engines report the measurement
    as unavailable. It deliberately never delegates to a paid provider.
    """

    provider_name = "FREE_ROUTING_UNAVAILABLE"

    def __init__(self, reason: str = "OneMap routing is not configured") -> None:
        self.reason = reason

    def _fail(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RoutingProviderError(self.reason, self.provider_name)

    get_walking_route = _fail
    get_driving_route = _fail
    get_baseline_driving_route = _fail
    get_driving_alternatives = _fail
    get_transit_route = _fail
    get_route_matrix = _fail
