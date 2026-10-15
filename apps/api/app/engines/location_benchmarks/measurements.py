"""Underlying measurements behind NearHome's existing location features.

Each entry names a raw measurement that the existing engines already
calculate, how to read it from the stored component result, and which
direction is better. No new metric is introduced: every value below is a
field the Public Transport or baseline Driving engines already produce.

Only the six approved measurements are ranked. Each is a separate
comparison; no overall transport or driving ranking is created and the two
bus measurements are never combined.

Features that cannot sensibly be ranked are listed in ``UNRANKED_FEATURES``
with the reason, so the UI and docs can disclose them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["lower", "higher"]
FeatureSet = Literal["public_transport", "driving_baseline"]

# Bump when an extraction below changes meaning. Stored measurements carry the
# feature-set calculation version; this version covers the extraction layer.
MEASUREMENT_VERSION = "measurements-v2"

BASELINE_MEASUREMENT = "baseline_estimated_driving_time"
# Mock routing is only ever used in demo mode, which is never published.
COMPARABLE_DRIVING_PROVIDERS = ("ONEMAP_ROUTING", "MOCK_ROUTING")


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
        return self.direction == "lower"

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


def _baseline_major_road(rollup: dict[str, Any]) -> MeasuredValue:
    """Only OneMap baseline results are comparable; AM-peak Google results never are."""
    value, reason = _assessed_value(rollup, "major_road_access")
    if value is None:
        return MeasuredValue(None, "unavailable", reason)
    if value.get("measurement") != BASELINE_MEASUREMENT or value.get("traffic_aware"):
        return MeasuredValue(None, "unavailable", "not a baseline major-road measurement")
    if value.get("provider") not in COMPARABLE_DRIVING_PROVIDERS:
        return MeasuredValue(None, "unavailable", f"provider {value.get('provider')} is not comparable")
    number = _number(value.get("baseline_duration_minutes"))
    if number is None:
        return MeasuredValue(None, "unavailable", "baseline duration missing")
    return MeasuredValue(number, "measured")


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
        "drive_major_road_baseline_minutes",
        "driving_baseline",
        "major_road_access",
        "Baseline estimated drive to a major road",
        "minutes",
        "lower",
        _baseline_major_road,
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
        "feature": "driving.route_flexibility",
        "reason": (
            "Google alternatives are fetched only for shortlisted homes and are not exhaustive; alternatives, "
            "overlap and extra travel time are shown as evidence only."
        ),
    },
    {
        "feature": "driving.peak_hour_access_penalty",
        "reason": "Estimated peak-hour delay from Google for shortlisted homes only; shown as evidence.",
    },
    {
        "feature": "driving.parking",
        "reason": "Product decision: parking has no comparison. Walk, carparks and availability stay as evidence.",
    },
    {
        "feature": "schools",
        "reason": "Not part of the approved comparisons; school counts and distances stay as evidence.",
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
