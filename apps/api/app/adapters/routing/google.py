"""Live Google Routes implementation of the shared RoutingProvider interface.

Uses the Routes API `computeRoutes` (single/alternative routes with turn-by-
turn steps) and `computeRouteMatrix` (bulk duration-only queries) endpoints.
All Google-specific request/response parsing is contained in this module —
the engines only ever see `RouteResult` / `RouteMatrixResponse`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from app.adapters.provider_schemas.google_routes import (
    GoogleRoute,
    GoogleRouteMatrixRow,
    GoogleRoutesResponse,
    parse_google_duration_seconds,
)
from app.adapters.routing.base import (
    RouteMatrixEntry,
    RouteMatrixResponse,
    RouteMode,
    RouteResult,
    RoutingProvider,
    RoutingProviderError,
    RoutingUnavailableError,
)
from app.adapters.routing.cache import (
    DEFAULT_TTL_SECONDS,
    TRAFFIC_AWARE_TTL_SECONDS,
    TRANSIT_TTL_SECONDS,
    build_cache_key,
    get_route_cache,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.services.enrichment_metrics import record_route_cache, record_route_request

logger = get_logger(__name__)

_COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_COMPUTE_ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

_ROUTES_FIELD_MASK = (
    "routes.duration,routes.distanceMeters,routes.routeLabels,routes.polyline.encodedPolyline,"
    "routes.legs.steps.travelMode,routes.legs.steps.staticDuration,"
    "routes.legs.steps.distanceMeters,routes.legs.steps.navigationInstruction,"
    "routes.legs.steps.transitDetails,routes.warnings"
)
_ROUTES_SUMMARY_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.routeLabels,routes.warnings"
_MATRIX_FIELD_MASK = "originIndex,destinationIndex,duration,distanceMeters,condition"

_MODE_MAP: dict[RouteMode, str] = {"WALK": "WALK", "DRIVE": "DRIVE", "TRANSIT": "TRANSIT"}


def _parse_duration_seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return parse_google_duration_seconds(value)
    except ValueError:
        return None


_MATRIX_ROWS_ADAPTER = TypeAdapter(list[GoogleRouteMatrixRow])


def _waypoint(coord: tuple[float, float]) -> dict[str, Any]:
    return {"location": {"latLng": {"latitude": coord[0], "longitude": coord[1]}}}


class GoogleRoutingProvider(RoutingProvider):
    provider_name = "GOOGLE_ROUTES"

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self.api_key = settings.google_maps_api_key
        self._cache = get_route_cache()
        self._client = client
        self._owns_client = client is None

    def _get_client(self, timeout: float) -> httpx.Client:
        """Create one client lazily and reuse its HTTP/TLS connection pool."""
        if self._client is None:
            self._client = httpx.Client(timeout=timeout)
        return self._client

    def close(self) -> None:
        """Release an internally-created connection pool at worker shutdown."""
        if self._owns_client and self._client is not None:
            self._client.close()
        self._client = None

    def __enter__(self) -> GoogleRoutingProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self, field_mask: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": field_mask,
        }

    @staticmethod
    def _google_error_fields(response_text: str) -> tuple[str | None, str | None]:
        """Extract Google's stable status/message without exposing the API key."""
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            return None, None
        error = payload.get("error", payload) if isinstance(payload, dict) else {}
        if not isinstance(error, dict):
            return None, None
        code = error.get("status")
        if code is None and error.get("code") is not None:
            code = str(error["code"])
        message = error.get("message")
        return str(code) if code is not None else None, str(message) if message is not None else None

    def _log_diagnostic(
        self,
        *,
        endpoint: str,
        body: dict[str, Any],
        field_mask: str,
        response: httpx.Response | None = None,
        response_body: str | None = None,
        exc_info: bool = False,
    ) -> None:
        """Log request/response details without ever logging the API key."""
        logger.error(
            "google_routes_diagnostic",
            endpoint=endpoint,
            method="POST",
            request_body=body,
            field_mask=field_mask,
            api_key_present=bool(self.api_key),
            status=response.status_code if response is not None else None,
            status_text=response.reason_phrase if response is not None else None,
            response_body=response_body if settings.app_env == "development" else None,
            request_id=(
                response.headers.get("x-request-id") or response.headers.get("x-goog-request-id")
                if response is not None
                else None
            ),
            exc_info=exc_info,
        )

    def _post_json(
        self,
        endpoint: str,
        body: dict[str, Any],
        field_mask: str,
        timeout: float,
    ) -> httpx.Response:
        """POST JSON and preserve Google's complete error response for diagnosis."""
        started = perf_counter()
        try:
            response = self._get_client(timeout).post(endpoint, headers=self._headers(field_mask), json=body)
        except httpx.HTTPError as exc:
            record_route_request(
                (perf_counter() - started) * 1000,
                success=False,
                timeout=isinstance(exc, httpx.TimeoutException),
            )
            self._log_diagnostic(
                endpoint=endpoint,
                body=body,
                field_mask=field_mask,
                response_body=str(exc),
                exc_info=True,
            )
            raise RoutingProviderError(
                f"Google Routes request failed: {exc}", self.provider_name, retryable=True
            ) from exc

        if response.status_code >= 400:
            record_route_request((perf_counter() - started) * 1000, success=False)
            # Read the body exactly once on the error path. Do not call
            # response.json() afterwards; the complete text is what makes
            # quota/auth/schema failures diagnosable.
            response_text = response.text
            error_code, provider_message = self._google_error_fields(response_text)
            error = RoutingProviderError(
                f"Google Routes error {response.status_code}: {provider_message or response_text[:200]}",
                self.provider_name,
                retryable=response.status_code in (403, 429, 500, 502, 503, 504),
                http_status=response.status_code,
                error_code=error_code,
                response_body=response_text,
                request_id=response.headers.get("x-request-id") or response.headers.get("x-goog-request-id"),
            )
            try:
                raise error
            except RoutingProviderError:
                self._log_diagnostic(
                    endpoint=endpoint,
                    body=body,
                    field_mask=field_mask,
                    response=response,
                    response_body=response_text,
                    exc_info=True,
                )
                raise

        record_route_request((perf_counter() - started) * 1000, success=True)
        return response

    def _compute_routes(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        travel_mode: str,
        departure_time: datetime | None,
        alternatives: bool,
        traffic_aware: bool,
        high_quality_polyline: bool = False,
        include_polyline: bool = True,
    ) -> list[GoogleRoute]:
        body: dict[str, Any] = {
            "origin": _waypoint(origin),
            "destination": _waypoint(destination),
            "travelMode": travel_mode,
            "computeAlternativeRoutes": alternatives,
            "units": "METRIC",
        }
        if travel_mode == "DRIVE":
            body["routingPreference"] = "TRAFFIC_AWARE" if traffic_aware else "TRAFFIC_UNAWARE"
        if high_quality_polyline:
            body["polylineQuality"] = "HIGH_QUALITY"
        # Google rejects departureTime when DRIVE uses TRAFFIC_UNAWARE.
        # A timestamp is meaningful for traffic-aware driving and transit;
        # omit it for non-traffic driving instead of sending an invalid pair.
        if departure_time is not None and not (travel_mode == "DRIVE" and not traffic_aware):
            body["departureTime"] = departure_time.astimezone(UTC).isoformat()

        field_mask = _ROUTES_FIELD_MASK if include_polyline else _ROUTES_SUMMARY_FIELD_MASK
        resp = self._post_json(_COMPUTE_ROUTES_URL, body, field_mask, timeout=20.0)
        try:
            raw_data = resp.json()
        except (TypeError, ValueError) as exc:
            self._log_diagnostic(
                endpoint=_COMPUTE_ROUTES_URL,
                body=body,
                field_mask=field_mask,
                response=resp,
                response_body=resp.text,
                exc_info=True,
            )
            raise RoutingProviderError(
                "Google Routes returned invalid JSON", self.provider_name, response_body=resp.text
            ) from exc
        try:
            data = GoogleRoutesResponse.model_validate(raw_data)
        except ValidationError as exc:
            self._log_diagnostic(
                endpoint=_COMPUTE_ROUTES_URL,
                body=body,
                field_mask=field_mask,
                response=resp,
                response_body=resp.text,
                exc_info=True,
            )
            raise RoutingProviderError(
                "Google Routes returned a malformed success response",
                self.provider_name,
                error_code="invalid_output",
                response_body=resp.text,
            ) from exc
        if not data.routes:
            raise RoutingUnavailableError("Google Routes returned no usable route", self.provider_name)
        return data.routes

    def _route_to_result(
        self,
        route: GoogleRoute | dict[str, Any],
        provider_mode: str,
        departure_time: datetime | None,
        traffic_aware: bool,
        is_alternative: bool,
    ) -> RouteResult:
        from app.adapters.routing.base import RouteStep

        if isinstance(route, dict):
            try:
                route = GoogleRoute.model_validate(route)
            except ValidationError as exc:
                # Treat a malformed successful route as unavailable rather
                # than allowing missing values to become zero-duration data.
                raise RoutingUnavailableError(
                    "Google Routes response omitted a valid route duration or distance",
                    self.provider_name,
                ) from exc
        duration_s = route.duration_seconds
        distance_m = route.distance_metres
        steps: list[RouteStep] = []
        walking_seconds = 0.0
        transit_seconds = 0.0
        transfers = 0
        last_transit_line: str | None = None
        for leg in route.legs:
            for step in leg.steps:
                step_mode = step.travel_mode or provider_mode
                step_duration = step.duration_seconds or 0.0
                step_distance = step.distance_metres
                instruction = step.navigation_instruction.instruction if step.navigation_instruction else ""
                transit = step.transit_details
                transit_line = None
                transit_service = None
                if transit:
                    line_info = transit.transit_line
                    if line_info:
                        transit_line = line_info.name or line_info.short_name
                        transit_service = line_info.short_name
                    line_changed = last_transit_line is not None and transit_line != last_transit_line
                    if line_changed:
                        transfers += 1
                    last_transit_line = transit_line or last_transit_line
                if step_mode == "WALK":
                    walking_seconds += step_duration
                elif step_mode == "TRANSIT":
                    transit_seconds += step_duration
                steps.append(
                    RouteStep(
                        instruction=instruction,
                        mode=step_mode,
                        distance_metres=step_distance,
                        duration_minutes=round(step_duration / 60, 1),
                        transit_line=transit_line,
                        transit_service_number=transit_service,
                    )
                )

        arrival_time = None
        if departure_time is not None and duration_s is not None:
            from datetime import timedelta

            arrival_time = departure_time + timedelta(seconds=duration_s)

        labels = route.route_labels
        return RouteResult(
            duration_minutes=round(duration_s / 60, 1),
            distance_metres=distance_m,
            transfers=transfers if provider_mode == "TRANSIT" else None,
            walking_minutes=round(walking_seconds / 60, 1) if steps else None,
            route_steps=steps,
            provider=self.provider_name,
            departure_time=departure_time,
            arrival_time=arrival_time,
            traffic_aware=traffic_aware and provider_mode == "DRIVE",
            warnings=route.warnings,
            raw_reference=None,
            is_alternative=is_alternative,
            route_label=labels[0] if labels else None,
            encoded_polyline=route.polyline.encoded_polyline if route.polyline else None,
            transit_minutes=round(transit_seconds / 60, 1) if provider_mode == "TRANSIT" else None,
        )

    def get_walking_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        cache_key = build_cache_key(self.provider_name, "walking", origin, destination, "WALK")
        cached = self._cache.get(cache_key)
        record_route_cache(hit=bool(cached))
        if cached:
            return _result_from_cache(cached)

        routes = self._compute_routes(origin, destination, "WALK", None, alternatives=False, traffic_aware=False)
        result = self._route_to_result(routes[0], "WALK", None, traffic_aware=False, is_alternative=False)
        self._cache.set(cache_key, _result_to_cache(result), ttl_seconds=DEFAULT_TTL_SECONDS)
        return result

    def get_driving_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult:
        cache_key = build_cache_key(
            self.provider_name, "driving", origin, destination, "DRIVE", departure_time, {"traffic": traffic_aware}
        )
        cached = self._cache.get(cache_key)
        record_route_cache(hit=bool(cached))
        if cached:
            return _result_from_cache(cached)

        routes = self._compute_routes(
            origin, destination, "DRIVE", departure_time, alternatives=False, traffic_aware=traffic_aware
        )
        result = self._route_to_result(routes[0], "DRIVE", departure_time, traffic_aware, is_alternative=False)
        ttl = TRAFFIC_AWARE_TTL_SECONDS if traffic_aware else DEFAULT_TTL_SECONDS
        self._cache.set(cache_key, _result_to_cache(result), ttl_seconds=ttl)
        return result

    def get_driving_route_summary(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult:
        """Google duration/distance ranking request without route geometry."""
        cache_key = build_cache_key(
            self.provider_name,
            "driving_summary",
            origin,
            destination,
            "DRIVE",
            departure_time,
            {"traffic": traffic_aware},
        )
        cached = self._cache.get(cache_key)
        record_route_cache(hit=bool(cached))
        if cached:
            return _result_from_cache(cached)
        routes = self._compute_routes(
            origin,
            destination,
            "DRIVE",
            departure_time,
            alternatives=False,
            traffic_aware=traffic_aware,
            include_polyline=False,
        )
        result = self._route_to_result(routes[0], "DRIVE", departure_time, traffic_aware, is_alternative=False)
        self._cache.set(
            cache_key,
            _result_to_cache(result),
            ttl_seconds=TRAFFIC_AWARE_TTL_SECONDS if traffic_aware else DEFAULT_TTL_SECONDS,
        )
        return result

    def get_driving_alternatives(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
    ) -> list[RouteResult]:
        cache_key = build_cache_key(self.provider_name, "driving_alts", origin, destination, "DRIVE", departure_time)
        cached = self._cache.get(cache_key)
        record_route_cache(hit=bool(cached))
        if cached and isinstance(cached.get("routes"), list):
            return [_result_from_cache(r) for r in cached["routes"]]

        routes = self._compute_routes(
            origin,
            destination,
            "DRIVE",
            departure_time,
            alternatives=True,
            traffic_aware=True,
            high_quality_polyline=True,
        )
        results = [
            self._route_to_result(r, "DRIVE", departure_time, True, is_alternative=idx > 0)
            for idx, r in enumerate(routes)
        ]
        self._cache.set(
            cache_key,
            {"routes": [_result_to_cache(r) for r in results]},
            ttl_seconds=TRAFFIC_AWARE_TTL_SECONDS,
        )
        return results

    def get_transit_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
    ) -> RouteResult:
        cache_key = build_cache_key(self.provider_name, "transit", origin, destination, "TRANSIT", departure_time)
        cached = self._cache.get(cache_key)
        record_route_cache(hit=bool(cached))
        if cached:
            return _result_from_cache(cached)

        routes = self._compute_routes(
            origin, destination, "TRANSIT", departure_time, alternatives=False, traffic_aware=False
        )
        result = self._route_to_result(routes[0], "TRANSIT", departure_time, traffic_aware=False, is_alternative=False)
        self._cache.set(cache_key, _result_to_cache(result), ttl_seconds=TRANSIT_TTL_SECONDS)
        return result

    def get_route_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        mode: RouteMode,
        departure_time: datetime | None = None,
    ) -> RouteMatrixResponse:
        travel_mode = _MODE_MAP[mode]
        body: dict[str, Any] = {
            "origins": [{"waypoint": _waypoint(o)} for o in origins],
            "destinations": [{"waypoint": _waypoint(d)} for d in destinations],
            "travelMode": travel_mode,
        }
        if travel_mode == "DRIVE":
            body["routingPreference"] = "TRAFFIC_AWARE"
        if departure_time is not None:
            body["departureTime"] = departure_time.astimezone(UTC).isoformat()

        resp = self._post_json(_COMPUTE_ROUTE_MATRIX_URL, body, _MATRIX_FIELD_MASK, timeout=20.0)
        try:
            data = resp.json()
        except (TypeError, ValueError) as exc:
            self._log_diagnostic(
                endpoint=_COMPUTE_ROUTE_MATRIX_URL,
                body=body,
                field_mask=_MATRIX_FIELD_MASK,
                response=resp,
                response_body=resp.text,
                exc_info=True,
            )
            raise RoutingProviderError(
                "Google Route Matrix returned invalid JSON", self.provider_name, response_body=resp.text
            ) from exc
        try:
            rows = _MATRIX_ROWS_ADAPTER.validate_python(data)
        except ValidationError as exc:
            raise RoutingProviderError(
                "Google Route Matrix returned a malformed success response",
                self.provider_name,
                error_code="invalid_output",
                response_body=resp.text,
            ) from exc
        entries = [
            RouteMatrixEntry(
                origin_index=row.origin_index,
                duration_minutes=round(row.duration_seconds / 60, 1) if row.duration_seconds is not None else None,
                distance_metres=row.distance_metres,
                status=row.condition,
            )
            for row in rows
        ]
        return RouteMatrixResponse(
            entries=entries,
            provider=self.provider_name,
            departure_time=departure_time,
            traffic_aware=travel_mode == "DRIVE",
        )


def _result_to_cache(result: RouteResult) -> dict[str, Any]:
    return {
        "duration_minutes": result.duration_minutes,
        "distance_metres": result.distance_metres,
        "transfers": result.transfers,
        "walking_minutes": result.walking_minutes,
        "route_steps": [
            {
                "instruction": s.instruction,
                "mode": s.mode,
                "distance_metres": s.distance_metres,
                "duration_minutes": s.duration_minutes,
                "transit_line": s.transit_line,
                "transit_service_number": s.transit_service_number,
            }
            for s in result.route_steps
        ],
        "provider": result.provider,
        "departure_time": result.departure_time.isoformat() if result.departure_time else None,
        "arrival_time": result.arrival_time.isoformat() if result.arrival_time else None,
        "traffic_aware": result.traffic_aware,
        "warnings": result.warnings,
        "is_alternative": result.is_alternative,
        "route_label": result.route_label,
        "encoded_polyline": result.encoded_polyline,
        "transit_minutes": result.transit_minutes,
    }


def _result_from_cache(cached: dict[str, Any]) -> RouteResult:
    from app.adapters.routing.base import RouteStep

    return RouteResult(
        duration_minutes=cached["duration_minutes"],
        distance_metres=cached["distance_metres"],
        transfers=cached.get("transfers"),
        walking_minutes=cached.get("walking_minutes"),
        route_steps=[
            RouteStep(
                instruction=s["instruction"],
                mode=s["mode"],
                distance_metres=s["distance_metres"],
                duration_minutes=s["duration_minutes"],
                transit_line=s.get("transit_line"),
                transit_service_number=s.get("transit_service_number"),
            )
            for s in cached.get("route_steps", [])
        ],
        provider=cached["provider"],
        departure_time=datetime.fromisoformat(cached["departure_time"]) if cached.get("departure_time") else None,
        arrival_time=datetime.fromisoformat(cached["arrival_time"]) if cached.get("arrival_time") else None,
        traffic_aware=cached.get("traffic_aware", False),
        warnings=cached.get("warnings", []),
        is_alternative=cached.get("is_alternative", False),
        route_label=cached.get("route_label"),
        encoded_polyline=cached.get("encoded_polyline"),
        transit_minutes=cached.get("transit_minutes"),
    )
