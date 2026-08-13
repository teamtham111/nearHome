"""Provider-independent routing interface.

The Public Transport and Driving engines depend only on `RoutingProvider`
and `RouteResult` — never on a specific provider's response shape. This
keeps Google (or any future provider)'s request/response parsing entirely
inside the adapter, per the routing-layer requirement.

Haversine distance must never be returned here as a walking/driving time —
every `RouteResult` in this module either comes from an actual routing-
provider call, or the method raises `RoutingProviderError` /
`RoutingUnavailableError` so the caller can produce a `not_assessed` /
`provider_error` component instead of a fabricated number.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

RouteMode = Literal["WALK", "DRIVE", "TRANSIT"]


class RoutingProviderError(Exception):
    """A routing provider call failed (network error, bad request, quota, etc.)."""

    def __init__(
        self,
        message: str,
        provider: str,
        retryable: bool = False,
        *,
        http_status: int | None = None,
        error_code: str | None = None,
        response_body: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.http_status = http_status
        self.error_code = error_code
        self.response_body = response_body
        self.request_id = request_id


class RoutingUnavailableError(RoutingProviderError):
    """The provider responded but found no usable route (e.g. NO_ROUTE)."""


@dataclass
class RouteStep:
    """One instruction/leg segment of a routed journey."""

    instruction: str
    mode: str
    distance_metres: int
    duration_minutes: float
    transit_line: str | None = None
    transit_service_number: str | None = None


@dataclass
class RouteResult:
    """Common internal representation returned by every RoutingProvider method."""

    duration_minutes: float
    distance_metres: int
    transfers: int | None
    walking_minutes: float | None
    route_steps: list[RouteStep]
    provider: str
    departure_time: datetime | None
    arrival_time: datetime | None
    traffic_aware: bool
    warnings: list[str] = field(default_factory=list)
    raw_reference: str | None = None
    is_alternative: bool = False
    route_label: str | None = None
    # Google Routes encoded route-level polyline. It is optional because mock
    # and non-Google providers may not supply route geometry.
    encoded_polyline: str | None = None
    # Transit decomposition is optional because walking/driving routes do not
    # have a transit leg. When present it is parsed from provider steps.
    transit_minutes: float | None = None


@dataclass
class RouteMatrixEntry:
    origin_index: int
    duration_minutes: float | None
    distance_metres: int | None
    status: str


@dataclass
class RouteMatrixResponse:
    entries: list[RouteMatrixEntry]
    provider: str
    departure_time: datetime | None
    traffic_aware: bool


class RoutingProvider(ABC):
    """Provider-independent routing interface.

    Coordinates are (latitude, longitude) tuples throughout.
    """

    provider_name: str

    @abstractmethod
    def get_walking_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult: ...

    @abstractmethod
    def get_driving_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult: ...

    def get_driving_route_summary(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult:
        """Cheap duration/distance pass; providers may omit route geometry."""
        return self.get_driving_route(origin, destination, departure_time, traffic_aware)

    @abstractmethod
    def get_driving_alternatives(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
    ) -> list[RouteResult]: ...

    @abstractmethod
    def get_transit_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
    ) -> RouteResult: ...

    @abstractmethod
    def get_route_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        mode: RouteMode,
        departure_time: datetime | None = None,
    ) -> RouteMatrixResponse: ...
