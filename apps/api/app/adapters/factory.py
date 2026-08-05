"""Adapter factory — live adapters in live mode and fixtures only in DEMO_MODE."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_geocoding_adapter():
    if settings.demo_mode or not settings.onemap_email:
        from app.adapters.mock.onemap import MockOneMapAdapter

        return MockOneMapAdapter()
    try:
        from app.adapters.live.onemap import LiveOneMapAdapter

        return LiveOneMapAdapter()
    except Exception as exc:
        logger.warning("onemap_live_fallback", error=str(exc))
        from app.adapters.mock.onemap import MockOneMapAdapter

        return MockOneMapAdapter()


def get_places_adapter():
    if settings.demo_mode or not settings.google_maps_api_key:
        from app.adapters.mock.google_places import MockGooglePlacesAdapter

        return MockGooglePlacesAdapter()
    try:
        from app.adapters.live.google_places import LiveGooglePlacesAdapter

        return LiveGooglePlacesAdapter()
    except Exception as exc:
        logger.warning("places_live_fallback", error=str(exc))
        from app.adapters.mock.google_places import MockGooglePlacesAdapter

        return MockGooglePlacesAdapter()


def get_routes_adapter():
    if settings.demo_mode or not settings.google_maps_api_key:
        from app.adapters.mock.google_routes import MockGoogleRoutesAdapter

        return MockGoogleRoutesAdapter()
    try:
        from app.adapters.live.google_routes import LiveGoogleRoutesAdapter

        return LiveGoogleRoutesAdapter()
    except Exception as exc:
        logger.warning("routes_live_fallback", error=str(exc))
        from app.adapters.mock.google_routes import MockGoogleRoutesAdapter

        return MockGoogleRoutesAdapter()


def get_routing_provider():
    """Shared RoutingProvider for the Public Transport / Driving engines.

    Distinct from `get_routes_adapter()` (the older duration-only route-
    matrix adapter still used by the personal important-location journeys
    feature) — this one exposes walking/driving/transit routes with steps,
    alternatives and traffic-awareness.
    """
    if settings.demo_mode or not settings.google_maps_api_key:
        from app.adapters.routing.mock import MockRoutingProvider

        # Mock routing is an explicit demo-mode choice only. It is never
        # substituted after a live Google request fails.
        return MockRoutingProvider()
    from app.adapters.routing.google import GoogleRoutingProvider

    # A live provider error must reach the engines as provider_error. Returning
    # a mock here would make Driving Access appear calculated from fake routes.
    return GoogleRoutingProvider()


@lru_cache(maxsize=1)
def get_transactions_adapter():
    from app.adapters.mock.hdb_transactions import FixtureHDBTransactionsAdapter

    return FixtureHDBTransactionsAdapter()


def get_llm_adapter():
    if settings.demo_mode:
        from app.adapters.mock.groq import MockGroqAdapter

        return MockGroqAdapter()
    from app.adapters.live.groq import LiveGroqAdapter

    return LiveGroqAdapter()
