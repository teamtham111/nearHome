"""Tests for the shared routing layer, caching keys, and route-overlap classification (Parts 3 and 7.2)."""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest

from app.adapters.live.google_routes import LiveGoogleRoutesAdapter
from app.adapters.routing.base import RouteResult, RouteStep, RoutingProviderError, RoutingUnavailableError
from app.adapters.routing.cache import build_cache_key
from app.adapters.routing.fallback import FallbackRoutingProvider
from app.adapters.routing.google import GoogleRoutingProvider
from app.adapters.routing.mock import MockRoutingProvider
from app.adapters.transport_data.major_road_network import DriveEdge, DriveNode, LocalDriveGraph
from app.core.config import settings
from app.networks.route_overlap import classify_alternative, decode_google_polyline, map_match_route

DHOBY_GHAUT = (1.29891164362677, 103.84629250696)
TAMPINES = (1.35619148271544, 103.9546344625)


def _route(
    minutes: float,
    distance: int,
    steps: list[RouteStep] | None = None,
    *,
    points: list[tuple[float, float]] | None = None,
) -> RouteResult:
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
        encoded_polyline=_encode_polyline(points) if points else None,
    )


def _encode_polyline(points: list[tuple[float, float]]) -> str:
    encoded = ""
    previous_latitude = previous_longitude = 0
    for latitude, longitude in points:
        values = ((round(latitude * 100_000), previous_latitude), (round(longitude * 100_000), previous_longitude))
        for value, previous in values:
            delta = value - previous
            shifted = ~(delta << 1) if delta < 0 else delta << 1
            while shifted >= 0x20:
                encoded += chr((0x20 | (shifted & 0x1F)) + 63)
                shifted >>= 5
            encoded += chr(shifted + 63)
        previous_latitude, previous_longitude = round(latitude * 100_000), round(longitude * 100_000)
    return encoded


def _graph(edges: list[tuple[str, str, str, list[tuple[float, float]], str | None]]) -> LocalDriveGraph:
    coordinates = {coordinate for _source, _target, _key, line, _name in edges for coordinate in line}
    nodes = [DriveNode(f"n{index}", latitude, longitude) for index, (latitude, longitude) in enumerate(coordinates)]
    node_ids = {(node.latitude, node.longitude): node.identifier for node in nodes}
    return LocalDriveGraph(
        nodes,
        [
            DriveEdge(
                node_ids[line[0]],
                node_ids[line[-1]],
                key,
                tuple((longitude, latitude) for latitude, longitude in line),
                name,
                111,
            )
            for _source, _target, key, line, name in edges
        ],
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
        def get_walking_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
            raise RoutingProviderError("simulated failure", provider="TEST", retryable=False)

    def test_provider_error_never_produces_a_route(self) -> None:
        provider = self._FailingProvider()
        with pytest.raises(RoutingProviderError):
            provider.get_walking_route(DHOBY_GHAUT, TAMPINES)


class TestGoogleRoutingProvider:
    @pytest.mark.parametrize(
        "route",
        [
            {"distanceMeters": 100},
            {"duration": "not-a-duration", "distanceMeters": 100},
            {"duration": "60s"},
            {"duration": "60s", "distanceMeters": -1},
        ],
    )
    def test_malformed_success_payload_cannot_become_a_zero_minute_route(self, route: dict) -> None:
        provider = GoogleRoutingProvider()
        with pytest.raises(RoutingUnavailableError):
            provider._route_to_result(route, "DRIVE", None, traffic_aware=True, is_alternative=False)

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

    def test_driving_alternatives_request_and_cache_high_quality_polyline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        class FakeClient:
            def post(self, _endpoint, *, headers, json, **_kwargs):
                captured.update(headers=headers, body=json)
                return httpx.Response(
                    200,
                    json={
                        "routes": [{"duration": "60s", "distanceMeters": 100, "polyline": {"encodedPolyline": "??"}}]
                    },
                    request=httpx.Request("POST", "https://example.test/routes"),
                )

        monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
        provider = GoogleRoutingProvider(client=FakeClient())
        provider._cache = type("EmptyCache", (), {"get": lambda *_args: None, "set": lambda *_args, **_kwargs: None})()
        result = provider.get_driving_alternatives(DHOBY_GHAUT, TAMPINES, datetime(2026, 8, 2, 8, 0))

        assert captured["body"]["polylineQuality"] == "HIGH_QUALITY"
        assert "routes.polyline.encodedPolyline" in captured["headers"]["X-Goog-FieldMask"]
        assert result[0].encoded_polyline == "??"

    def test_driving_summary_omits_polyline_for_major_road_candidate_ranking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        class FakeClient:
            def post(self, _endpoint, *, headers, json, **_kwargs):
                captured.update(headers=headers, body=json)
                return httpx.Response(
                    200,
                    json={"routes": [{"duration": "60s", "distanceMeters": 100}]},
                    request=httpx.Request("POST", "https://example.test/routes"),
                )

        monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
        provider = GoogleRoutingProvider(client=FakeClient())
        provider._cache = type("EmptyCache", (), {"get": lambda *_args: None, "set": lambda *_args, **_kwargs: None})()
        result = provider.get_driving_route_summary(DHOBY_GHAUT, TAMPINES, datetime(2026, 8, 2, 8, 0))

        assert result.encoded_polyline is None
        assert "routes.polyline.encodedPolyline" not in captured["headers"]["X-Goog-FieldMask"]

    def test_reuses_one_client_and_closes_an_owned_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created: list[object] = []

        class FakeClient:
            closed = False

            def __init__(self) -> None:
                created.append(self)

            def post(self, endpoint, **_kwargs):
                return httpx.Response(
                    200,
                    json={"routes": [{"duration": "60s", "distanceMeters": 1}]},
                    request=httpx.Request("POST", endpoint),
                )

            def close(self) -> None:
                self.closed = True

        monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
        monkeypatch.setattr("app.adapters.routing.google.httpx.Client", lambda **_kwargs: FakeClient())
        provider = GoogleRoutingProvider()
        provider._cache = type("EmptyCache", (), {"get": lambda *_args: None, "set": lambda *_args, **_kwargs: None})()

        provider.get_walking_route(DHOBY_GHAUT, TAMPINES)
        provider.get_walking_route(TAMPINES, DHOBY_GHAUT)
        provider.close()

        assert len(created) == 1
        assert created[0].closed is True

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


class TestRoutePolylineOverlap:
    def test_google_polyline_decoder_round_trips_coordinates(self) -> None:
        points = [(1.3, 103.8), (1.3, 103.801), (1.301, 103.801)]
        decoded = decode_google_polyline(_encode_polyline(points))
        assert decoded == pytest.approx(points)

    def test_same_directed_osm_route_is_substantially_overlapping(self) -> None:
        points = [(1.3, 103.8), (1.3, 103.801), (1.3, 103.802)]
        graph = _graph(
            [
                ("a", "b", "east-1", points[:2], "Alpha Road"),
                ("b", "c", "east-2", points[1:], "Alpha Road"),
            ]
        )
        result = classify_alternative(_route(5, 222, points=points), _route(6, 222, points=points), graph=graph)
        assert result.overlap_method == "osm_edge_match"
        assert result.classification == "substantially_overlapping"
        assert result.overlap_ratio == 1.0
        assert result.primary_match is not None
        assert result.primary_match.confidence == "HIGH"

    def test_partial_shared_edge_is_partially_independent(self) -> None:
        primary_points = [(1.3, 103.8), (1.3, 103.801), (1.3, 103.802)]
        alternative_points = [(1.3, 103.8), (1.3, 103.801), (1.301, 103.801)]
        graph = _graph(
            [
                ("a", "b", "shared", primary_points[:2], "Alpha Road"),
                ("b", "c", "primary", primary_points[1:], "Alpha Road"),
                ("b", "d", "alternative", alternative_points[1:], "Bravo Road"),
            ]
        )
        result = classify_alternative(
            _route(5, 222, points=primary_points), _route(6, 222, points=alternative_points), graph=graph
        )
        assert result.overlap_method == "osm_edge_match"
        assert result.classification == "partially_independent"
        assert 0.45 <= result.overlap_ratio <= 0.55

    def test_separate_network_path_is_independent(self) -> None:
        primary_points = [(1.3, 103.8), (1.3, 103.801), (1.3, 103.802)]
        alternative_points = [(1.299, 103.8), (1.299, 103.801), (1.299, 103.802)]
        graph = _graph(
            [
                ("a", "b", "primary-1", primary_points[:2], "Alpha Road"),
                ("b", "c", "primary-2", primary_points[1:], "Alpha Road"),
                ("d", "e", "alternative-1", alternative_points[:2], "Bravo Road"),
                ("e", "f", "alternative-2", alternative_points[1:], "Bravo Road"),
            ]
        )
        result = classify_alternative(
            _route(5, 222, points=primary_points), _route(6, 222, points=alternative_points), graph=graph
        )
        assert result.overlap_method == "osm_edge_match"
        assert result.classification == "independent"
        assert result.overlap_ratio == 0.0

    def test_close_parallel_edges_are_not_merged_by_geometric_tolerance(self) -> None:
        primary_points = [(1.3, 103.8), (1.3, 103.801), (1.3, 103.802)]
        parallel_points = [(1.30015, 103.8), (1.30015, 103.801), (1.30015, 103.802)]
        graph = _graph(
            [
                ("a", "b", "primary-1", primary_points[:2], "Alpha Road"),
                ("b", "c", "primary-2", primary_points[1:], "Alpha Road"),
                ("d", "e", "parallel-1", parallel_points[:2], "Frontage Road"),
                ("e", "f", "parallel-2", parallel_points[1:], "Frontage Road"),
            ]
        )
        result = classify_alternative(
            _route(5, 222, points=primary_points), _route(6, 222, points=parallel_points), graph=graph
        )
        assert result.overlap_method == "osm_edge_match"
        assert result.classification == "independent"
        assert result.overlap_ratio == 0.0

    def test_crossing_roads_do_not_create_material_overlap(self) -> None:
        primary_points = [(1.3, 103.8), (1.3, 103.801), (1.3, 103.802)]
        crossing_points = [(1.299, 103.801), (1.3, 103.801), (1.301, 103.801)]
        graph = _graph(
            [
                ("a", "b", "primary-1", primary_points[:2], "Alpha Road"),
                ("b", "c", "primary-2", primary_points[1:], "Alpha Road"),
                ("d", "b", "crossing-1", crossing_points[:2], "Cross Street"),
                ("b", "e", "crossing-2", crossing_points[1:], "Cross Street"),
            ]
        )
        result = classify_alternative(
            _route(5, 222, points=primary_points), _route(6, 222, points=crossing_points), graph=graph
        )
        assert result.overlap_method == "osm_edge_match"
        assert result.classification == "independent"
        assert result.overlap_ratio == 0.0

    def test_opposite_carriageways_are_kept_as_distinct_directed_edges(self) -> None:
        eastbound = [(1.3, 103.8), (1.3, 103.801), (1.3, 103.802)]
        westbound = [(1.30012, 103.802), (1.30012, 103.801), (1.30012, 103.8)]
        graph = _graph(
            [
                ("a", "b", "east-1", eastbound[:2], "Alpha Expressway"),
                ("b", "c", "east-2", eastbound[1:], "Alpha Expressway"),
                ("d", "e", "west-1", westbound[:2], "Alpha Expressway"),
                ("e", "f", "west-2", westbound[1:], "Alpha Expressway"),
            ]
        )
        result = classify_alternative(_route(5, 222, points=eastbound), _route(6, 222, points=westbound), graph=graph)
        assert result.overlap_method == "osm_edge_match"
        assert result.classification == "independent"

    def test_connected_ramp_beats_nearer_disconnected_frontage_edge(self) -> None:
        points = [(1.3, 103.8), (1.3, 103.801), (1.301, 103.801)]
        frontage = [(1.3, 103.80112), (1.301, 103.80112)]
        graph = _graph(
            [
                ("a", "b", "approach", points[:2], "Local Road"),
                ("b", "c", "ramp", points[1:], "Ramp"),
                ("x", "y", "frontage", frontage, "Frontage Road"),
            ]
        )
        match = map_match_route(_route(5, 222, points=points), graph)
        assert match is not None
        assert any(edge_id[2] == "ramp" for edge_id in match.edge_distances_metres)
        assert not any(edge_id[2] == "frontage" for edge_id in match.edge_distances_metres)

    def test_low_confidence_map_match_uses_polyline_geometry_and_records_method(self) -> None:
        points = [(1.3, 103.8), (1.3, 103.801), (1.3, 103.802)]
        graph = _graph([("a", "b", "short", points[:2], "Alpha Road")])
        result = classify_alternative(_route(5, 222, points=points), _route(6, 222, points=points), graph=graph)
        assert result.overlap_method == "polyline_geometry"
        assert result.geometric_overlap_ratio == 1.0
        assert result.primary_match is not None
        assert result.primary_match.confidence == "LOW"

    def test_missing_polyline_uses_explicit_legacy_road_name_fallback(self) -> None:
        primary = _route(5, 222, [RouteStep("Head onto Alpha Road", "DRIVE", 222, 5)])
        alternative = _route(6, 222, [RouteStep("Continue on Alpha Road", "DRIVE", 222, 6)])
        result = classify_alternative(primary, alternative)
        assert result.overlap_method == "road_name_fallback"
        assert result.classification == "substantially_overlapping"
