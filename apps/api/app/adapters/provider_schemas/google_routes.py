"""Google Routes and Route Matrix success-response schemas."""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from app.adapters.provider_schemas.common import ProviderPayload


def parse_google_duration_seconds(value: Any) -> float:
    """Parse Google's protobuf Duration JSON representation into seconds."""

    raw: object
    if isinstance(value, str) and value.endswith("s"):
        raw = value[:-1]
    elif isinstance(value, dict):
        raw = value.get("seconds")
    else:
        raise ValueError("duration must be a '<seconds>s' string or an object containing seconds")
    if raw is None:
        raise ValueError("duration seconds must be present")
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration seconds must be numeric") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("duration seconds must be finite and non-negative")
    return seconds


GoogleDurationSeconds = Annotated[
    float,
    BeforeValidator(parse_google_duration_seconds),
    Field(ge=0, allow_inf_nan=False),
]


class GoogleNavigationInstruction(ProviderPayload):
    instruction: str = ""


class GoogleTransitLine(ProviderPayload):
    name: str | None = None
    short_name: str | None = Field(default=None, alias="nameShort")


class GoogleTransitDetails(ProviderPayload):
    transit_line: GoogleTransitLine | None = Field(default=None, alias="transitLine")


class GoogleRouteStep(ProviderPayload):
    travel_mode: str | None = Field(default=None, alias="travelMode")
    duration_seconds: GoogleDurationSeconds | None = Field(default=None, alias="staticDuration")
    distance_metres: int = Field(default=0, alias="distanceMeters", ge=0)
    navigation_instruction: GoogleNavigationInstruction | None = Field(default=None, alias="navigationInstruction")
    transit_details: GoogleTransitDetails | None = Field(default=None, alias="transitDetails")


class GoogleRouteLeg(ProviderPayload):
    steps: list[GoogleRouteStep] = Field(default_factory=list)


class GoogleRoutePolyline(ProviderPayload):
    encoded_polyline: str = Field(alias="encodedPolyline")


class GoogleRoute(ProviderPayload):
    duration_seconds: GoogleDurationSeconds = Field(alias="duration")
    distance_metres: int = Field(alias="distanceMeters", ge=0)
    legs: list[GoogleRouteLeg] = Field(default_factory=list)
    route_labels: list[str] = Field(default_factory=list, alias="routeLabels")
    warnings: list[str] = Field(default_factory=list)
    polyline: GoogleRoutePolyline | None = None


class GoogleRoutesResponse(ProviderPayload):
    # Empty is structurally valid and is classified separately as NO_ROUTE.
    routes: list[GoogleRoute]


class GoogleRouteMatrixRow(ProviderPayload):
    origin_index: int = Field(default=0, alias="originIndex", ge=0)
    destination_index: int = Field(default=0, alias="destinationIndex", ge=0)
    duration_seconds: GoogleDurationSeconds | None = Field(default=None, alias="duration")
    distance_metres: int | None = Field(default=None, alias="distanceMeters", ge=0)
    condition: str = Field(min_length=1)
