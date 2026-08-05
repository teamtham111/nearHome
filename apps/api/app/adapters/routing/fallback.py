"""Routing provider wrapper that falls back when the live provider is unavailable."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from app.adapters.routing.base import (
    RouteMatrixResponse,
    RouteMode,
    RouteResult,
    RoutingProvider,
    RoutingProviderError,
    RoutingUnavailableError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class FallbackRoutingProvider(RoutingProvider):
    """Try the primary provider first; use fallback on routing failures."""

    provider_name = "ROUTING_WITH_FALLBACK"

    def __init__(self, primary: RoutingProvider, fallback: RoutingProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    def _call(self, method: str, primary_fn: Callable[[], T], fallback_fn: Callable[[], T]) -> T:
        try:
            return primary_fn()
        except (RoutingProviderError, RoutingUnavailableError) as exc:
            logger.warning(
                "routing_provider_fallback",
                method=method,
                primary=self._primary.provider_name,
                fallback=self._fallback.provider_name,
                error=str(exc),
            )
            return fallback_fn()

    def get_walking_route(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> RouteResult:
        return self._call(
            "get_walking_route",
            lambda: self._primary.get_walking_route(origin, destination),
            lambda: self._fallback.get_walking_route(origin, destination),
        )

    def get_driving_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult:
        return self._call(
            "get_driving_route",
            lambda: self._primary.get_driving_route(origin, destination, departure_time, traffic_aware),
            lambda: self._fallback.get_driving_route(origin, destination, departure_time, traffic_aware),
        )

    def get_driving_alternatives(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
    ) -> list[RouteResult]:
        return self._call(
            "get_driving_alternatives",
            lambda: self._primary.get_driving_alternatives(origin, destination, departure_time),
            lambda: self._fallback.get_driving_alternatives(origin, destination, departure_time),
        )

    def get_transit_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
    ) -> RouteResult:
        return self._call(
            "get_transit_route",
            lambda: self._primary.get_transit_route(origin, destination, departure_time),
            lambda: self._fallback.get_transit_route(origin, destination, departure_time),
        )

    def get_route_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        mode: RouteMode,
        departure_time: datetime | None = None,
    ) -> RouteMatrixResponse:
        return self._call(
            "get_route_matrix",
            lambda: self._primary.get_route_matrix(origins, destinations, mode, departure_time),
            lambda: self._fallback.get_route_matrix(origins, destinations, mode, departure_time),
        )
