"""Live Google Routes route matrix adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from app.adapters.base import RouteMatrixElement, RouteMatrixResult
from app.adapters.routing.base import RoutingProviderError
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _google_error_code(response_text: str) -> str | None:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        return None
    error = payload.get("error", payload)
    if not isinstance(error, dict):
        return None
    if error.get("status") is not None:
        return str(error["status"])
    return str(error["code"]) if error.get("code") is not None else None


class LiveGoogleRoutesAdapter:
    provider = "GOOGLE_ROUTES"

    def __init__(self) -> None:
        self.api_key = settings.google_maps_api_key

    def route_matrix(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
        mode: str,
        departure_at: datetime,
    ) -> RouteMatrixResult:
        travel_mode = "TRANSIT" if mode == "PUBLIC_TRANSPORT" else "DRIVE"
        url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "originIndex,duration,condition",
        }
        origins_payload = [
            {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lng}}}}
            for lat, lng in origins
        ]
        body = {
            "origins": origins_payload,
            "destinations": [
                {
                    "waypoint": {
                        "location": {
                            "latLng": {
                                "latitude": destination[0],
                                "longitude": destination[1],
                            }
                        }
                    }
                }
            ],
            "travelMode": travel_mode,
            "departureTime": departure_at.astimezone(UTC).isoformat(),
        }
        if travel_mode == "DRIVE":
            body["routingPreference"] = "TRAFFIC_AWARE"
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            logger.exception(
                "google_routes_matrix_diagnostic",
                endpoint=url,
                method="POST",
                request_body=body,
                field_mask=headers["X-Goog-FieldMask"],
                api_key_present=bool(self.api_key),
                status=None,
                status_text=None,
                response_body=None,
            )
            raise RoutingProviderError(
                f"Google Route Matrix request failed: {exc}", self.provider, retryable=True
            ) from exc

        if resp.status_code >= 400:
            # Consume the response as text once on the error path so the
            # provider's complete quota/auth/schema message is retained.
            response_text = resp.text
            error = RoutingProviderError(
                f"Google Route Matrix error {resp.status_code}: {response_text[:200]}",
                self.provider,
                retryable=resp.status_code in (403, 429, 500, 502, 503, 504),
                http_status=resp.status_code,
                error_code=_google_error_code(response_text),
                response_body=response_text,
                request_id=resp.headers.get("x-request-id") or resp.headers.get("x-goog-request-id"),
            )
            try:
                raise error
            except RoutingProviderError:
                logger.exception(
                    "google_routes_matrix_diagnostic",
                    endpoint=url,
                    method="POST",
                    request_body=body,
                    field_mask=headers["X-Goog-FieldMask"],
                    api_key_present=bool(self.api_key),
                    status=resp.status_code,
                    status_text=resp.reason_phrase,
                    response_body=response_text if settings.app_env == "development" else None,
                    request_id=resp.headers.get("x-request-id") or resp.headers.get("x-goog-request-id"),
                )
                raise

        try:
            data = resp.json()
        except (TypeError, ValueError) as exc:
            logger.exception(
                "google_routes_matrix_diagnostic",
                endpoint=url,
                method="POST",
                request_body=body,
                field_mask=headers["X-Goog-FieldMask"],
                api_key_present=bool(self.api_key),
                status=resp.status_code,
                status_text=resp.reason_phrase,
                response_body=resp.text if settings.app_env == "development" else None,
            )
            raise RoutingProviderError(
                "Google Route Matrix returned invalid JSON", self.provider, response_body=resp.text
            ) from exc

        elements: list[RouteMatrixElement] = []
        if isinstance(data, list):
            for item in data:
                idx = item.get("originIndex", 0)
                dur = item.get("duration")
                seconds = int(dur.rstrip("s")) if isinstance(dur, str) and dur.endswith("s") else None
                if seconds is None and isinstance(dur, dict):
                    seconds = int(dur.get("seconds", 0)) or None
                status = "AVAILABLE" if seconds else "UNAVAILABLE"
                elements.append(
                    RouteMatrixElement(
                        origin_index=idx,
                        duration_seconds=seconds,
                        status=status,
                        provider_status=item.get("condition", "OK"),
                    )
                )
        else:
            for idx in range(len(origins)):
                elements.append(
                    RouteMatrixElement(
                        origin_index=idx,
                        duration_seconds=None,
                        status="UNAVAILABLE",
                        provider_status="NO_ROUTE",
                    )
                )

        return RouteMatrixResult(
            elements=elements,
            provider=self.provider,
            retrieved_at=datetime.now(UTC),
            resolved_departure_at=departure_at,
        )
