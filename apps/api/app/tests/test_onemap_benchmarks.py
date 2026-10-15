"""OneMap routing, provider separation and approved-ranking rules.

OneMap fixtures are trimmed copies of real responses from a 2026-10-16 live
probe. No test here makes a network call or a paid provider call.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from app.adapters.routing.base import RouteResult, RoutingProvider, RoutingProviderError, RoutingUnavailableError
from app.adapters.routing.onemap import (
    OneMapRoutingProvider,
    UnavailableRoutingProvider,
    parse_transit,
    parse_walk_or_drive,
)
from app.domain.enums import ComponentStatus
from app.engines.driving.engine import compute_driving_baseline, compute_driving_paid_evidence
from app.engines.driving.major_road_access import BASELINE_MEASUREMENT, compute_major_road_access
from app.engines.location_benchmarks.measurements import MEASUREMENTS_BY_KEY, UNRANKED_FEATURES, extract_measurements
from app.engines.time_utils import SINGAPORE_TZ
from app.tests.routing_helpers import _driving_polyline
from app.tests.test_driving_engine import (  # noqa: F401  (autouse fixture keeps artifacts synthetic)
    SYNTHETIC_MAPPING,
    _local_major_road_artifacts,
)

FIXTURES = Path(__file__).parent / "fixtures" / "onemap"
NOW = datetime(2026, 10, 16, 9, 0, tzinfo=SINGAPORE_TZ)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# OneMap parsing -----------------------------------------------------------------


def test_walk_seconds_become_minutes_and_geometry_is_kept() -> None:
    sample = _fixture("walk")
    route = parse_walk_or_drive(sample["response"], "WALK", NOW)
    assert route.duration_minutes == round(sample["response"]["route_summary"]["total_time"] / 60, 1)
    assert route.distance_metres == sample["response"]["route_summary"]["total_distance"]
    assert route.encoded_polyline == sample["response"]["route_geometry"]
    assert route.retrieved_at == NOW and route.provider == "ONEMAP_ROUTING"
    assert route.route_steps and route.route_steps[0].distance_metres > 0


def test_drive_is_a_baseline_not_traffic_aware() -> None:
    route = parse_walk_or_drive(_fixture("drive")["response"], "DRIVE", NOW)
    assert route.traffic_aware is False
    assert route.departure_time is None
    assert any("not traffic-aware" in warning for warning in route.warnings)


def test_transit_uses_route_short_name_epoch_ms_and_excludes_waiting() -> None:
    response = _fixture("feeder_transit")["response"]
    route = parse_transit(response, NOW)
    fastest = min(response["plan"]["itineraries"], key=lambda item: item["duration"])
    bus = next(step for step in route.route_steps if step.mode == "TRANSIT")
    assert bus.transit_service_number == "158"  # not "SBST BUS 158"
    assert bus.vehicle_type == "BUS"
    # Duration is the itinerary's own span; earlier waiting is not added.
    assert route.duration_minutes == round(fastest["duration"] / 60, 1)
    assert route.transit_minutes == round(fastest["transitTime"] / 60, 1)
    assert route.departure_time is not None and route.departure_time.tzinfo is not None
    assert route.departure_time.utcoffset().total_seconds() == 8 * 3600
    assert route.departure_time.timestamp() * 1000 == fastest["startTime"]
    assert route.schedule_based is True
    assert not any("live" in w.lower() and "not" not in w.lower() for w in route.warnings)


def test_bus_and_rail_legs_stay_distinct() -> None:
    route = parse_transit(_fixture("bus_and_rail_transit")["response"], NOW)
    vehicles = [step.vehicle_type for step in route.route_steps if step.mode == "TRANSIT"]
    assert vehicles == ["BUS", "SUBWAY"]
    rail = route.route_steps[-1]
    assert rail.transit_service_number == "EW" and rail.transit_line == "EAST WEST LINE"


def test_missing_itineraries_and_routes_are_unavailable_not_zero() -> None:
    with pytest.raises(RoutingUnavailableError):
        parse_transit({"plan": {"itineraries": []}}, NOW)
    with pytest.raises(RoutingUnavailableError):
        parse_walk_or_drive({"status": 0}, "WALK", NOW)
    with pytest.raises(RoutingUnavailableError):
        parse_walk_or_drive({"route_summary": {"total_time": None, "total_distance": 10}}, "WALK", NOW)


def _provider(handler, monkeypatch: pytest.MonkeyPatch) -> OneMapRoutingProvider:
    from app.adapters.routing import onemap
    from app.core.config import settings

    monkeypatch.setattr(settings, "onemap_email", "test@example.com")
    monkeypatch.setattr(settings, "onemap_password", "test")
    monkeypatch.setattr(onemap, "_TOKEN", {"value": "test-token"})
    monkeypatch.setattr(onemap.time, "sleep", lambda _s: None)

    class NoCache:
        def get(self, _key):
            return None

        def set(self, *_args, **_kwargs):
            return None

    provider = OneMapRoutingProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_seconds=0
    )
    provider._cache = NoCache()
    return provider


def test_transit_request_uses_singapore_date_and_time(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_fixture("feeder_transit")["response"])

    provider = _provider(handler, monkeypatch)
    departure = datetime(2026, 10, 19, 0, 0, tzinfo=datetime.now().astimezone().tzinfo).astimezone(SINGAPORE_TZ)
    departure = departure.replace(hour=8, minute=0)
    provider.get_transit_route((1.3198, 103.8819), (1.3268, 103.8900), departure)
    assert seen["date"] == "10-19-2026" and seen["time"] == "08:00:00" and seen["routeType"] == "pt"


def test_unsupported_capabilities_fail_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(lambda request: httpx.Response(200, json={}), monkeypatch)
    with pytest.raises(RoutingUnavailableError):
        provider.get_driving_alternatives((1.3, 103.8), (1.31, 103.81), NOW)
    with pytest.raises(RoutingUnavailableError):
        provider.get_driving_route((1.3, 103.8), (1.31, 103.81), NOW, traffic_aware=True)
    with pytest.raises(RoutingUnavailableError):
        provider.get_route_matrix([(1.3, 103.8)], [(1.31, 103.81)], "WALK")


def test_server_errors_back_off_then_report_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    provider = _provider(handler, monkeypatch)
    with pytest.raises(RoutingProviderError) as error:
        provider.get_walking_route((1.3, 103.8), (1.31, 103.81))
    assert len(calls) == 5 and error.value.http_status == 503


def test_unconfigured_free_provider_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.adapters import factory
    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "onemap_email", "")
    provider = factory.get_free_routing_provider()
    assert isinstance(provider, UnavailableRoutingProvider)
    with pytest.raises(RoutingProviderError):
        provider.get_walking_route((1.3, 103.8), (1.31, 103.81))


# Public transport via OneMap ------------------------------------------------------


def test_public_transport_model_runs_on_onemap_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.engines.public_transport.engine import compute_public_transport_model

    walk = _fixture("walk")["response"]
    transit = _fixture("feeder_transit")["response"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=transit if request.url.params.get("routeType") == "pt" else walk)

    provider = _provider(handler, monkeypatch)
    rollup = compute_public_transport_model(1.320959, 103.884129, provider)
    access = next(c for c in rollup.components if c.name == "access")
    assert access.status == ComponentStatus.CALCULATED
    assert all(
        entry.get("provider") == "ONEMAP_ROUTING" for entry in access.evidence if entry.get("provider") is not None
    )
    assert provider.request_count > 0


# Baseline major-road access ---------------------------------------------------------


class BaselineProvider(RoutingProvider):
    """Free provider stub: static routes with geometry; no traffic, no alternatives."""

    provider_name = "ONEMAP_ROUTING"

    def __init__(self, polyline=_driving_polyline) -> None:
        self.polyline = polyline
        self.calls: list[str] = []

    def get_baseline_driving_route(self, origin, destination) -> RouteResult:
        self.calls.append("baseline")
        return RouteResult(
            6.0,
            3000,
            None,
            None,
            [],
            self.provider_name,
            None,
            None,
            False,
            encoded_polyline=self.polyline(origin, destination),
        )

    def get_walking_route(self, origin, destination) -> RouteResult:
        self.calls.append("walk")
        return RouteResult(3.0, 200, None, 3.0, [], self.provider_name, None, None, False)

    def _unsupported(self, *args, **kwargs):
        raise RoutingUnavailableError("unsupported", self.provider_name)

    get_driving_route = _unsupported
    get_driving_alternatives = _unsupported
    get_transit_route = _unsupported
    get_route_matrix = _unsupported


def test_baseline_major_road_access_validates_entry_and_is_labelled_baseline() -> None:
    provider = BaselineProvider()
    outcome = compute_major_road_access(1.3000, 103.7990, provider, baseline=True)
    assert outcome.result.status == ComponentStatus.CALCULATED
    value = outcome.result.value
    assert value["measurement"] == BASELINE_MEASUREMENT and value["traffic_aware"] is False
    assert "peak_duration_minutes" not in value
    assert value["selected_entry"]["major_road_id"] == outcome.selected_point.major_road_id
    assert "not an AM-peak" in " ".join(outcome.result.limitations)
    assert set(provider.calls) == {"baseline"}  # no alternatives/detail requests


def test_baseline_rejects_routes_that_do_not_enter_the_major_road() -> None:
    def crossing(origin, destination):
        from app.tests.routing_helpers import _encode_polyline

        return _encode_polyline([(1.299, 103.805), (1.301, 103.805)])

    outcome = compute_major_road_access(1.3000, 103.7990, BaselineProvider(crossing), baseline=True)
    assert outcome.result.status == ComponentStatus.NOT_ASSESSED
    assert outcome.selected_point is None


def test_free_driving_baseline_never_touches_google(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.adapters.routing import google

    def refuse(*_args, **_kwargs):
        raise AssertionError("Google must not be constructed for free measurements")

    monkeypatch.setattr(google.GoogleRoutingProvider, "__init__", refuse)
    provider = BaselineProvider()
    components = compute_driving_baseline(1.3000, 103.7990, provider)
    assert [c.name for c in components] == ["major_road_access", "parking_convenience"]


# Google-only peak and off-peak evidence -------------------------------------------


class GoogleStub(RoutingProvider):
    provider_name = "GOOGLE_ROUTES"

    def __init__(self, traffic_aware: bool = True, fail_off_peak: bool = False) -> None:
        self.requests: list[tuple[tuple[float, float], tuple[float, float], int, bool]] = []
        self.traffic_aware = traffic_aware
        self.fail_off_peak = fail_off_peak

    def get_driving_route(self, origin, destination, departure_time, traffic_aware=True) -> RouteResult:
        self.requests.append((origin, destination, departure_time.hour, traffic_aware))
        if self.fail_off_peak and departure_time.hour != 8:
            raise RoutingProviderError("quota", self.provider_name)
        minutes = 14.0 if departure_time.hour == 8 else 9.0
        return RouteResult(minutes, 3000, None, None, [], self.provider_name, departure_time, None, self.traffic_aware)

    def get_driving_alternatives(self, origin, destination, departure_time):
        self.alternative_requests = getattr(self, "alternative_requests", 0) + 1
        return [RouteResult(9.0, 3000, None, None, [], self.provider_name, departure_time, None, True)]

    def _unsupported(self, *args, **kwargs):
        raise RoutingUnavailableError("unsupported", self.provider_name)

    get_walking_route = _unsupported
    get_transit_route = _unsupported
    get_route_matrix = _unsupported


def _peak(google: GoogleStub):
    baseline = compute_major_road_access(1.3000, 103.7990, BaselineProvider(), baseline=True)
    _connectivity, peak = compute_driving_paid_evidence(1.3000, 103.7990, google, baseline.result)
    return baseline, peak


def test_peak_delay_uses_google_for_both_times_to_the_baseline_entry() -> None:
    google = GoogleStub()
    baseline, peak = _peak(google)
    peak_calls = [r for r in google.requests if r[2] in (8, 22)]
    destinations = {r[1] for r in peak_calls}
    assert destinations == {baseline.selected_point.routing_coordinate}
    assert {r[2] for r in peak_calls} == {8, 22} and all(r[3] for r in peak_calls)
    assert peak.value["penalty_minutes"] == 5.0  # 14 Google peak - 9 Google off-peak, never OneMap
    assert peak.value["peak_duration_minutes"] == 14.0 and peak.value["off_peak_duration_minutes"] == 9.0
    assert "reliab" not in peak.explanation.lower()


@pytest.mark.parametrize("google", [GoogleStub(fail_off_peak=True), GoogleStub(traffic_aware=False)])
def test_missing_or_static_google_results_leave_peak_delay_unavailable(google: GoogleStub) -> None:
    _baseline, peak = _peak(google)
    assert peak.status == ComponentStatus.PROVIDER_ERROR
    assert peak.value is None and peak.score is None


# Approved rankings only ----------------------------------------------------------------


def test_only_the_six_approved_measurements_are_ranked() -> None:
    assert set(MEASUREMENTS_BY_KEY) == {
        "pt_access_cost_minutes",
        "pt_direct_bus_corridors",
        "pt_one_transfer_bus_corridors",
        "pt_mrt_stations_within_30_min",
        "pt_independent_fallbacks",
        "drive_major_road_baseline_minutes",
    }
    unranked = {item["feature"] for item in UNRANKED_FEATURES}
    assert {"driving.parking", "driving.route_flexibility", "driving.peak_hour_access_penalty"} <= unranked


def test_google_am_peak_major_road_results_are_never_comparable() -> None:
    legacy = {
        "components": [
            {
                "name": "major_road_access",
                "status": "calculated",
                "score": 80,
                "value": {"routed_duration_minutes": 6.0, "peak_duration_minutes": 6.0, "provider": "GOOGLE_ROUTES"},
            }
        ]
    }
    measured = extract_measurements("driving_baseline", legacy)["drive_major_road_baseline_minutes"]
    assert measured["status"] == "unavailable"
    google_baseline = json.loads(json.dumps(legacy))
    google_baseline["components"][0]["value"].update(
        {"measurement": BASELINE_MEASUREMENT, "traffic_aware": False, "baseline_duration_minutes": 6.0}
    )
    assert (
        extract_measurements("driving_baseline", google_baseline)["drive_major_road_baseline_minutes"]["status"]
        == "unavailable"
    )
