"""Tests for the shared routing layer, caching keys, and route-overlap classification (Parts 3 and 7.2)."""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest

from app.adapters.live.google_routes import LiveGoogleRoutesAdapter
from app.adapters.routing.base import RouteResult, RouteStep, RoutingProviderError
from app.adapters.routing.cache import build_cache_key
from app.adapters.routing.fallback import FallbackRoutingProvider
from app.adapters.routing.google import GoogleRoutingProvider
from app.adapters.routing.mock import MockRoutingProvider
from app.core.config import settings
from app.networks.route_overlap import classify_alternative

DHOBY_GHAUT = (1.29891164362677, 103.84629250696)
TAMPINES = (1.35619148271544, 103.9546344625)


def _route(minutes: float, distance: int, steps: list[RouteStep] | None = None) -> RouteResult:
    return RouteResult(
        duration_minutes=minutes,
        distance_metres=distance,
        transfers=None,
        walking_minutes=None,
        route_steps=steps or [],
        provider="TEST",
        departure_time=None,
        arrival_time=None,
        traffic_aware=True,
    )


class TestMockRoutingProvider:
    """The mock provider is deterministic and used only for tests/demo mode
    — it never claims to be a substitute for live routing (see module docstring)."""

    def test_walking_route_is_deterministic(self) -> None:
        provider = MockRoutingProvider()
        a = provider.get_walking_route(DHOBY_GHAUT, TAMPINES)
        b = provider.get_walking_route(DHOBY_GHAUT, TAMPINES)
        assert a.duration_minutes == b.duration_minutes
        assert a.distance_metres == b.distance_metres

    def test_driving_alternatives_returns_a_slower_alternate(self) -> None:
        provider = MockRoutingProvider()
        alternatives = provider.get_driving_alternatives(DHOBY_GHAUT, TAMPINES, datetime.now())
        assert len(alternatives) == 2
        primary, alt = alternatives
        assert alt.duration_minutes > primary.duration_minutes
        assert alt.is_alternative is True
        assert primary.is_alternative is False


class TestRoutingProviderErrorHandling:
    class _FailingProvider(MockRoutingProvider):
        def get_walking_route(
            self, origin: tuple[float, float], destination: tuple[float, float]
        ) -> RouteResult:
            raise RoutingProviderError("simulated failure", provider="TEST", retryable=False)

    def test_provider_error_never_produces_a_route(self) -> None:
        provider = self._FailingProvider()
        with pytest.raises(RoutingProviderError):
            provider.get_walking_route(DHOBY_GHAUT, TAMPINES)


class TestGoogleRoutingProvider:
    def test_request_shape_and_google_error_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        error_body = {
            "error": {
                "code": 429,
                "message": "Quota exceeded",
                "status": "RESOURCE_EXHAUSTED",
            }
        }

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, endpoint, *, headers, json, **kwargs):
                captured.update(endpoint=endpoint, headers=headers, body=json, kwargs=kwargs)
                return httpx.Response(
                    429,
                    headers={"content-type": "application/json"},
                    content=json_module.dumps(error_body).encode(),
                    request=httpx.Request("POST", endpoint),
                )

        json_module = json
        monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
        monkeypatch.setattr("app.adapters.routing.google.httpx.Client", lambda **kwargs: FakeClient())

        provider = GoogleRoutingProvider()
        with pytest.raises(RoutingProviderError) as caught:
            provider._compute_routes(
                (1.3521, 103.8198),
                (1.3009, 103.8563),
                "DRIVE",
                datetime(2026, 8, 2, 12, 0),
                alternatives=False,
                traffic_aware=False,
            )

        assert captured["endpoint"] == "https://routes.googleapis.com/directions/v2:computeRoutes"
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Goog-Api-Key"] == "test-key"
        assert headers["X-Goog-FieldMask"].startswith("routes.duration,routes.distanceMeters")
        body = captured["body"]
        assert isinstance(body, dict)
        assert body["travelMode"] == "DRIVE"
        assert body["routingPreference"] == "TRAFFIC_UNAWARE"
        assert "departureTime" not in body
        assert body["origin"]["location"]["latLng"] == {"latitude": 1.3521, "longitude": 103.8198}
        assert body["destination"]["location"]["latLng"] == {"latitude": 1.3009, "longitude": 103.8563}
        assert caught.value.http_status == 429
        assert caught.value.error_code == "RESOURCE_EXHAUSTED"
        assert json.loads(caught.value.response_body or "{}")["error"]["message"] == "Quota exceeded"

    def test_valid_response_is_parsed_without_reading_error_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        route_body = {"routes": [{"duration": "123s", "distanceMeters": 456}]}

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, endpoint, **kwargs):
                return httpx.Response(
                    200,
                    json=route_body,
                    request=httpx.Request("POST", endpoint),
                )

        monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
        monkeypatch.setattr("app.adapters.routing.google.httpx.Client", lambda **kwargs: FakeClient())

        routes = GoogleRoutingProvider()._compute_routes(
            (1.3521, 103.8198),
            (1.3009, 103.8563),
            "DRIVE",
            None,
            alternatives=False,
            traffic_aware=False,
        )
        assert routes == route_body["routes"]

    def test_route_matrix_sets_traffic_aware_for_departure_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, endpoint, *, headers, json, **kwargs):
                captured.update(endpoint=endpoint, headers=headers, body=json)
                return httpx.Response(
                    200,
                    json=[{"originIndex": 0, "duration": "60s", "condition": "ROUTE_EXISTS"}],
                    request=httpx.Request("POST", endpoint),
                )

        monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
        monkeypatch.setattr("app.adapters.live.google_routes.httpx.Client", lambda **kwargs: FakeClient())

        result = LiveGoogleRoutesAdapter().route_matrix(
            [(1.3521, 103.8198)],
            (1.3009, 103.8563),
            "DRIVING",
            datetime(2026, 8, 2, 12, 0),
        )

        assert result.elements[0].duration_seconds == 60
        assert captured["endpoint"] == "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
        body = captured["body"]
        assert isinstance(body, dict)
        assert body["travelMode"] == "DRIVE"
        assert body["routingPreference"] == "TRAFFIC_AWARE"
        assert "departureTime" in body


class TestFallbackRoutingProvider:
    def test_falls_back_when_primary_fails(self) -> None:
        provider = FallbackRoutingProvider(TestRoutingProviderErrorHandling._FailingProvider(), MockRoutingProvider())
        route = provider.get_walking_route(DHOBY_GHAUT, TAMPINES)
        assert route.provider == "MOCK_ROUTING"
        assert route.duration_minutes > 0


class TestRoutingFactory:
    def test_live_mode_does_not_replace_google_with_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.factory import get_routing_provider

        monkeypatch.setattr(settings, "demo_mode", False)
        monkeypatch.setattr(settings, "google_maps_api_key", "server-key-present")
        provider = get_routing_provider()
        assert isinstance(provider, GoogleRoutingProvider)
        assert provider.provider_name == "GOOGLE_ROUTES"


class TestCacheKey:
    def test_key_is_stable_for_the_same_inputs(self) -> None:
        t = datetime(2026, 1, 5, 8, 0)
        k1 = build_cache_key("GOOGLE", "walking", DHOBY_GHAUT, TAMPINES, "WALK", t)
        k2 = build_cache_key("GOOGLE", "walking", DHOBY_GHAUT, TAMPINES, "WALK", t)
        assert k1 == k2

    def test_key_differs_by_mode_and_time_bucket(self) -> None:
        am_peak = datetime(2026, 1, 5, 8, 0)  # Monday
        off_peak = datetime(2026, 1, 5, 23, 0)
        k_walk = build_cache_key("GOOGLE", "route", DHOBY_GHAUT, TAMPINES, "WALK", am_peak)
        k_drive_peak = build_cache_key("GOOGLE", "route", DHOBY_GHAUT, TAMPINES, "DRIVE", am_peak)
        k_drive_offpeak = build_cache_key("GOOGLE", "route", DHOBY_GHAUT, TAMPINES, "DRIVE", off_peak)
        assert k_walk != k_drive_peak
        assert k_drive_peak != k_drive_offpeak

    def test_key_rounds_coordinates_to_a_small_grid(self) -> None:
        origin_a = (1.300001, 103.800001)
        origin_b = (1.300002, 103.800002)
        k1 = build_cache_key("GOOGLE", "walking", origin_a, TAMPINES, "WALK")
        k2 = build_cache_key("GOOGLE", "walking", origin_b, TAMPINES, "WALK")
        assert k1 == k2


class TestRouteOverlapClassification:
    def test_high_overlap_is_not_independent(self) -> None:
        primary = _route(20, 8000, [RouteStep("Head onto Pan Island Expressway (PIE)", "DRIVE", 8000, 20)])
        alternative = _route(21, 8100, [RouteStep("Continue on Pan Island Expressway (PIE)", "DRIVE", 8100, 21)])
        result = classify_alternative(primary, alternative)
        assert result.classification == "substantially_overlapping"

    def test_disjoint_roads_are_independent(self) -> None:
        primary = _route(20, 8000, [RouteStep("Head onto Pan Island Expressway (PIE)", "DRIVE", 8000, 20)])
        alternative = _route(22, 8200, [RouteStep("Head onto Central Expressway (CTE)", "DRIVE", 8200, 22)])
        result = classify_alternative(primary, alternative)
        assert result.classification == "independent"

    def test_large_duration_penalty_is_not_practical(self) -> None:
        primary = _route(15, 6000, [RouteStep("Head onto Central Expressway (CTE)", "DRIVE", 6000, 15)])
        alternative = _route(40, 15000, [RouteStep("Head onto Pan Island Expressway (PIE)", "DRIVE", 15000, 40)])
        result = classify_alternative(primary, alternative, not_practical_penalty_minutes=15.0)
        assert result.classification == "not_practical"

    def test_moderate_overlap_is_partially_independent(self) -> None:
        primary = _route(
            20,
            8000,
            [
                RouteStep("Head onto Woodlands Avenue 5", "DRIVE", 2000, 5),
                RouteStep("Turn onto Central Expressway (CTE)", "DRIVE", 6000, 15),
            ],
        )
        alternative = _route(
            21,
            8100,
            [
                RouteStep("Head onto Woodlands Avenue 5", "DRIVE", 2000, 5),
                RouteStep("Turn onto Bukit Timah Expressway (BKE)", "DRIVE", 6100, 16),
            ],
        )
        result = classify_alternative(primary, alternative)
        assert result.classification in ("partially_independent", "independent")
        assert 0.0 <= result.overlap_ratio <= 1.0
