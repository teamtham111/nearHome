"""Shared test doubles for RoutingProvider — not a test module itself.

Named without a `test_` prefix so pytest does not try to collect it.
"""

from __future__ import annotations

from datetime import datetime

from app.adapters.routing.base import (
    RouteMatrixEntry,
    RouteMatrixResponse,
    RouteMode,
    RouteResult,
    RouteStep,
    RoutingProvider,
    RoutingProviderError,
)

# Real, geocoded coordinates (data_pipeline/fixtures/rail/rail_stations.json)
DHOBY_GHAUT = (1.29891164362677, 103.84629250696)
YISHUN = (1.42944308477331, 103.835005047246)
TAMPINES = (1.35619148271544, 103.9546344625)
WOODLANDS = (1.43681962961519, 103.786066799253)


class FixedDurationRoutingProvider(RoutingProvider):
    """Always returns the same duration regardless of coordinates.

    Used to prove an engine reports *whatever the provider says*, never a
    Haversine-derived number of its own — if an engine were secretly doing
    `distance / assumed_speed`, evidence durations would vary by
    destination; with this stub they never do.
    """

    provider_name = "FIXED_STUB"

    def __init__(self, minutes: float = 4.2, distance_metres: int = 333) -> None:
        self.minutes = minutes
        self.distance_metres = distance_metres
        self.calls: list[str] = []

    def get_walking_route(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> RouteResult:
        self.calls.append("walking")
        return RouteResult(
            duration_minutes=self.minutes,
            distance_metres=self.distance_metres,
            transfers=None,
            walking_minutes=self.minutes,
            route_steps=[RouteStep("Walk", "WALK", self.distance_metres, self.minutes)],
            provider=self.provider_name,
            departure_time=None,
            arrival_time=None,
            traffic_aware=False,
        )

    def get_driving_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult:
        self.calls.append("driving")
        return RouteResult(
            duration_minutes=self.minutes,
            distance_metres=self.distance_metres,
            transfers=None,
            walking_minutes=None,
            route_steps=[RouteStep("Drive", "DRIVE", self.distance_metres, self.minutes)],
            provider=self.provider_name,
            departure_time=departure_time,
            arrival_time=None,
            traffic_aware=traffic_aware,
        )

    def get_driving_alternatives(
        self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
    ) -> list[RouteResult]:
        primary = self.get_driving_route(origin, destination, departure_time)
        alt = self.get_driving_route(origin, destination, departure_time)
        alt.duration_minutes = round(alt.duration_minutes * 1.2, 1)
        alt.is_alternative = True
        return [primary, alt]

    def get_transit_route(
        self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
    ) -> RouteResult:
        self.calls.append("transit")
        return RouteResult(
            duration_minutes=self.minutes,
            distance_metres=self.distance_metres,
            transfers=0,
            walking_minutes=self.minutes,
            route_steps=[],
            provider=self.provider_name,
            departure_time=departure_time,
            arrival_time=None,
            traffic_aware=False,
        )

    def get_route_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        mode: RouteMode,
        departure_time: datetime | None = None,
    ) -> RouteMatrixResponse:
        entries = [
            RouteMatrixEntry(
                origin_index=i,
                duration_minutes=self.minutes,
                distance_metres=self.distance_metres,
                status="OK",
            )
            for i in range(len(origins))
        ]
        return RouteMatrixResponse(
            entries=entries, provider=self.provider_name, departure_time=departure_time, traffic_aware=False
        )


class AlwaysFailingRoutingProvider(RoutingProvider):
    """Every method raises — proves engines surface `provider_error` /
    `not_assessed` instead of fabricating a result when routing is unavailable."""

    provider_name = "ALWAYS_FAILING"

    def _fail(self) -> RouteResult:
        raise RoutingProviderError("simulated provider outage", provider=self.provider_name, retryable=True)

    def get_walking_route(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> RouteResult:
        return self._fail()

    def get_driving_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult:
        return self._fail()

    def get_driving_alternatives(
        self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
    ) -> list[RouteResult]:
        return self._fail()  # type: ignore[return-value]

    def get_transit_route(
        self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
    ) -> RouteResult:
        return self._fail()

    def get_route_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        mode: RouteMode,
        departure_time: datetime | None = None,
    ) -> RouteMatrixResponse:
        return self._fail()  # type: ignore[return-value]
