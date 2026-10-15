"""Underlying measurements behind NearHome's existing location features.

Each entry names a raw measurement that the existing engines already
calculate, how to read it from the stored component result, and which
direction is better. No new metric is introduced: every value below is a
field the Public Transport, Driving or Schools engines already produce.

Directions:
- ``lower`` / ``higher``: a smaller / larger value is a better result.
- ``buyer_dependent``: the value is ordered, but whether more is better is a
  buyer preference (for example being near schools). These are compared
  with neutral wording ("closer than", "more than") rather than "better".

Features that cannot sensibly be ranked are listed in ``UNRANKED_FEATURES``
with the reason, so the UI and docs can disclose them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["lower", "higher", "buyer_dependent_lower", "buyer_dependent_higher"]
FeatureSet = Literal["public_transport", "driving", "schools"]

# Bump when an extraction below changes meaning. Stored measurements carry the
# feature-set calculation version; this version covers the extraction layer.
MEASUREMENT_VERSION = "measurements-v1"


@dataclass(frozen=True)
class MeasuredValue:
    """One block's measurement.

    ``status`` is ``measured`` for a numeric value, ``worst`` when the engine
    calculated a genuine "no practical option" result (ranked below every
    numeric value, tied with other ``worst`` blocks), or ``unavailable`` when
    the calculation failed or was not assessed. Unavailable values never enter
    a comparison group and are never treated as zero.
    """

    value: float | None
    status: Literal["measured", "worst", "unavailable"]
    reason: str | None = None


@dataclass(frozen=True)
class MeasurementDefinition:
    key: str
    feature_set: FeatureSet
    component: str
    label: str
    unit: str
    direction: Direction
    extract: Callable[[dict[str, Any]], MeasuredValue]
    comparison_phrase: str = "Better than"

    @property
    def lower_is_better(self) -> bool:
        return self.direction in ("lower", "buyer_dependent_lower")

    @property
    def buyer_dependent(self) -> bool:
        return self.direction.startswith("buyer_dependent")

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "feature_set": self.feature_set,
            "component": self.component,
            "label": self.label,
            "unit": self.unit,
            "direction": self.direction,
            "comparison_phrase": self.comparison_phrase,
        }


_ASSESSED = {"calculated", "estimated"}


def _component(rollup: dict[str, Any], name: str) -> dict[str, Any] | None:
    for component in rollup.get("components") or []:
        if component.get("name") == name:
            return component
    return None


def _assessed_value(rollup: dict[str, Any], name: str) -> tuple[dict[str, Any] | None, str | None]:
    component = _component(rollup, name)
    if component is None:
        return None, f"{name} was not calculated"
    if str(component.get("status")) not in _ASSESSED or component.get("score") is None:
        return None, f"{name} status {component.get('status')}"
    value = component.get("value")
    return (value if isinstance(value, dict) else {}), None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _field(component: str, field: str) -> Callable[[dict[str, Any]], MeasuredValue]:
    def extract(rollup: dict[str, Any]) -> MeasuredValue:
        value, reason = _assessed_value(rollup, component)
        if value is None:
            return MeasuredValue(None, "unavailable", reason)
        number = _number(value.get(field))
        if number is None:
            return MeasuredValue(None, "unavailable", f"{component}.{field} missing")
        return MeasuredValue(number, "measured")

    return extract


def _access_cost(rollup: dict[str, Any]) -> MeasuredValue:
    value, reason = _assessed_value(rollup, "access")
    if value is None:
        return MeasuredValue(None, "unavailable", reason)
    selected = value.get("selected_access_path")
    if not selected:
        # Existing Access semantics: routing succeeded but no practical entry
        # path exists. That is a genuine worst result, not missing data.
        return MeasuredValue(None, "worst", "no practical bus or rail entry path")
    number = _number(selected.get("generalised_access_cost"))
    if number is None:
        return MeasuredValue(None, "unavailable", "generalised access cost missing")
    return MeasuredValue(number, "measured")


def _mrt_stations_within_30(rollup: dict[str, Any]) -> MeasuredValue:
    value, reason = _assessed_value(rollup, "mrt_reach")
    if value is None:
        return MeasuredValue(None, "unavailable", reason)
    parts = [
        _number(value.get(key))
        for key in ("zero_transfer_30", "one_transfer_30_incremental", "multi_transfer_30_incremental")
    ]
    if any(part is None for part in parts):
        return MeasuredValue(None, "unavailable", "MRT reach bucket counts missing")
    return MeasuredValue(float(sum(part for part in parts if part is not None)), "measured")


def _parking_walk(rollup: dict[str, Any]) -> MeasuredValue:
    value, reason = _assessed_value(rollup, "parking_convenience")
    if value is None:
        return MeasuredValue(None, "unavailable", reason)
    number = _number((value.get("primary_carpark") or {}).get("walk_minutes"))
    if number is None:
        return MeasuredValue(None, "unavailable", "primary carpark walk minutes missing")
    return MeasuredValue(number, "measured")


def _schools_field(field: str) -> Callable[[dict[str, Any]], MeasuredValue]:
    def extract(schools: dict[str, Any]) -> MeasuredValue:
        if schools.get("score_status") not in ("calculated", "CALCULATED"):
            return MeasuredValue(None, "unavailable", "school reference data unavailable")
        number = _number(schools.get(field))
        if number is None:
            # No school within the 2 km search radius: the nearest distance is
            # genuinely beyond the search radius, which is the farthest result.
            if field == "nearest_school_distance_km":
                return MeasuredValue(None, "worst", "no school within 2 km")
            return MeasuredValue(None, "unavailable", f"{field} missing")
        return MeasuredValue(number, "measured")

    return extract


MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        "pt_access_cost_minutes",
        "public_transport",
        "access",
        "Time to enter the public-transport network",
        "generalised minutes",
        "lower",
        _access_cost,
    ),
    MeasurementDefinition(
        "pt_direct_bus_corridors",
        "public_transport",
        "bus_coverage",
        "Distinct direct bus corridors",
        "corridors",
        "higher",
        _field("bus_coverage", "direct_corridors"),
    ),
    MeasurementDefinition(
        "pt_one_transfer_bus_corridors",
        "public_transport",
        "bus_coverage",
        "Extra bus corridors with one same-stop transfer",
        "corridors",
        "higher",
        _field("bus_coverage", "one_transfer_new_corridors"),
    ),
    MeasurementDefinition(
        "pt_mrt_stations_within_30_min",
        "public_transport",
        "mrt_reach",
        "MRT/LRT stations within 30 rail minutes",
        "stations",
        "higher",
        _mrt_stations_within_30,
    ),
    MeasurementDefinition(
        "pt_independent_fallbacks",
        "public_transport",
        "route_resilience",
        "Independent public-transport fallbacks",
        "fallbacks",
        "higher",
        _field("route_resilience", "independent_units"),
    ),
    MeasurementDefinition(
        "drive_major_road_minutes",
        "driving",
        "major_road_access",
        "Drive to the nearest major road",
        "minutes",
        "lower",
        _field("major_road_access", "routed_duration_minutes"),
    ),
    MeasurementDefinition(
        "drive_distinct_major_roads",
        "driving",
        "route_connectivity",
        "Distinct major roads reached",
        "roads",
        "higher",
        _field("route_connectivity", "distinct_major_roads_reached"),
    ),
    MeasurementDefinition(
        "drive_independent_routes",
        "driving",
        "route_connectivity",
        "Independent alternative driving routes",
        "routes",
        "higher",
        _field("route_connectivity", "independent_alternatives"),
    ),
    MeasurementDefinition(
        "drive_peak_delay_minutes",
        "driving",
        "peak_access_penalty",
        "Extra peak-hour minutes to the major road",
        "minutes",
        "lower",
        _field("peak_access_penalty", "penalty_minutes"),
    ),
    MeasurementDefinition(
        "drive_parking_walk_minutes",
        "driving",
        "parking_convenience",
        "Walk to the likely carpark",
        "minutes",
        "lower",
        _parking_walk,
    ),
    MeasurementDefinition(
        "schools_within_1km",
        "schools",
        "schools",
        "Schools within 1 km",
        "schools",
        "buyer_dependent_higher",
        _schools_field("schools_within_1km"),
        comparison_phrase="More schools within 1 km than",
    ),
    MeasurementDefinition(
        "schools_nearest_km",
        "schools",
        "schools",
        "Distance to the nearest school",
        "km",
        "buyer_dependent_lower",
        _schools_field("nearest_school_distance_km"),
        comparison_phrase="Closer to a school than",
    ),
)

MEASUREMENTS_BY_KEY = {definition.key: definition for definition in MEASUREMENTS}

UNRANKED_FEATURES: tuple[dict[str, str], ...] = (
    {
        "feature": "public_transport.overall",
        "reason": "Weighted composite of heuristic component scores, not a measurement; shown via its components.",
    },
    {
        "feature": "driving.overall",
        "reason": "Weighted composite of heuristic component scores, not a measurement; shown via its components.",
    },
    {
        "feature": "driving.parking_live_availability",
        "reason": "Point-in-time lot availability changes minute to minute and is not a stable block measurement.",
    },
    {
        "feature": "driving.partially_independent_routes",
        "reason": "Partial independence has no defensible order against full independence; shown as raw evidence.",
    },
    {
        "feature": "schools.named_school_distance",
        "reason": "Depends on the schools each buyer names, so it is unit/session-specific, not a block benchmark.",
    },
    {
        "feature": "schools.within_1km_and_nearest",
        "reason": "Ordered, but more/closer schools is a buyer preference; compared with neutral wording only.",
    },
    {
        "feature": "journeys",
        "reason": "Personal journeys depend on buyer destinations and departure times.",
    },
)


def extract_measurements(feature_set: str, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return ``{measurement_key: {value, status, reason}}`` for one feature set."""
    measured: dict[str, dict[str, Any]] = {}
    for definition in MEASUREMENTS:
        if definition.feature_set != feature_set:
            continue
        result = definition.extract(payload)
        measured[definition.key] = {"value": result.value, "status": result.status, "reason": result.reason}
    return measured
