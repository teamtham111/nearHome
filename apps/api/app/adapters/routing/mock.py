"""Deterministic mock RoutingProvider — used in demo mode and in tests.

Durations are derived from Haversine distance *only inside this mock*, purely
to produce plausible, deterministic fixture numbers. This is explicitly a
demo-data shortcut, not a claim that Haversine is an acceptable substitute
for routing elsewhere — the live `GoogleRoutingProvider` never does this.
"""

from __future__ import annotations

from datetime import datetime

from app.adapters.reference_data import haversine_m
from app.adapters.routing.base import (
    RouteMatrixEntry,
    RouteMatrixResponse,
    RouteMode,
    RouteResult,
    RouteStep,
    RoutingProvider,
)

_WALK_SPEED_M_MIN = 80.0
_DRIVE_SPEED_M_MIN = 500.0  # ~30 km/h effective urban peak speed
_TRANSIT_SPEED_M_MIN = 350.0


class MockRoutingProvider(RoutingProvider):
    provider_name = "MOCK_ROUTING"

    def get_walking_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        distance = haversine_m(*origin, *destination)
        minutes = round(distance / _WALK_SPEED_M_MIN, 1)
        return RouteResult(
            duration_minutes=minutes,
            distance_metres=round(distance),
            transfers=None,
            walking_minutes=minutes,
            route_steps=[
                RouteStep(
                    instruction="Walk to destination",
                    mode="WALK",
                    distance_metres=round(distance),
                    duration_minutes=minutes,
                )
            ],
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
        distance = haversine_m(*origin, *destination)
        minutes = round(distance / _DRIVE_SPEED_M_MIN + 2, 1)
        return RouteResult(
            duration_minutes=minutes,
            distance_metres=round(distance),
            transfers=None,
            walking_minutes=None,
            route_steps=[
                RouteStep(
                    instruction="Drive to destination",
                    mode="DRIVE",
                    distance_metres=round(distance),
                    duration_minutes=minutes,
                )
            ],
            provider=self.provider_name,
            departure_time=departure_time,
            arrival_time=None,
            traffic_aware=traffic_aware,
        )

    def get_driving_alternatives(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
    ) -> list[RouteResult]:
        primary = self.get_driving_route(origin, destination, departure_time)
        alt = self.get_driving_route(origin, destination, departure_time)
        alt.duration_minutes = round(alt.duration_minutes * 1.15, 1)
        alt.is_alternative = True
        alt.route_label = "DEFAULT_ROUTE_ALTERNATE"
        primary.route_label = "DEFAULT_ROUTE"
        return [primary, alt]

    def get_transit_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
    ) -> RouteResult:
        distance = haversine_m(*origin, *destination)
        minutes = round(distance / _TRANSIT_SPEED_M_MIN + 5, 1)
        return RouteResult(
            duration_minutes=minutes,
            distance_metres=round(distance),
            transfers=0,
            walking_minutes=round(minutes * 0.2, 1),
            route_steps=[
                RouteStep(
                    instruction="Take transit to destination",
                    mode="TRANSIT",
                    distance_metres=round(distance),
                    duration_minutes=minutes,
                )
            ],
            provider=self.provider_name,
            departure_time=departure_time,
            arrival_time=None,
            traffic_aware=False,
            transit_minutes=round(minutes * 0.8, 1),
        )

    def get_route_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        mode: RouteMode,
        departure_time: datetime | None = None,
    ) -> RouteMatrixResponse:
        speed = {"WALK": _WALK_SPEED_M_MIN, "DRIVE": _DRIVE_SPEED_M_MIN, "TRANSIT": _TRANSIT_SPEED_M_MIN}[mode]
        entries = []
        for idx, origin in enumerate(origins):
            dest = destinations[0] if destinations else origin
            distance = haversine_m(*origin, *dest)
            entries.append(
                RouteMatrixEntry(
                    origin_index=idx,
                    duration_minutes=round(distance / speed, 1),
                    distance_metres=round(distance),
                    status="OK",
                )
            )
        return RouteMatrixResponse(
            entries=entries, provider=self.provider_name, departure_time=departure_time, traffic_aware=False
        )
