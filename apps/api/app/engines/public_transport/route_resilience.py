"""Independence-based public-transport route resilience."""

from __future__ import annotations

from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed
from app.engines.transport_config import PT_CONFIG, PublicTransportConfig


def compute_route_resilience(
    bus_coverage_result: ComponentResult,
    mrt_reach_result: ComponentResult,
    config: PublicTransportConfig = PT_CONFIG,
    *,
    access_result: ComponentResult | None = None,
) -> ComponentResult:
    """Score independent alternatives, not the raw strengths of other models.

    The orchestrator supplies Access evidence explicitly. Without it the
    component cannot establish independence and therefore remains unassessed.
    """
    weight = config.weight_route_resilience
    if access_result is None:
        return not_assessed(
            "route_resilience",
            weight,
            "Access alternatives are required to assess independent route resilience.",
        )
    else:
        access_value = access_result.value or {}
        practical_entries = access_value.get("practical_rail_entries", [])
        bus_entries = [entry for entry in access_result.evidence if entry.get("path_type") == "walk_to_bus"]
        access_modes = {entry.get("access_mode") for entry in practical_entries}
        has_bus = bool(bus_entries or access_value.get("best_bus_entry"))
        has_rail = bool(practical_entries)
        independent_units = 0
        if has_bus and has_rail:
            independent_units += 1  # bus and rail are independent access modes

        station_ids = {
            entry.get("physical_station_id")
            for entry in practical_entries
            if entry.get("physical_station_id")
        }
        primary_id = (access_value.get("primary_rail_entry") or {}).get("physical_station_id")
        alternative_stations = [sid for sid in station_ids if sid != primary_id]
        if alternative_stations:
            independent_units += 1

        primary_lines = set((access_value.get("primary_rail_entry") or {}).get("direct_lines", []))
        alternative_lines = {
            line
            for entry in practical_entries
            if entry.get("physical_station_id") != primary_id
            for line in entry.get("direct_lines", [])
        }
        alternative_lines -= primary_lines
        if alternative_lines:
            independent_units += 1

        direct_corridors = int((bus_coverage_result.value or {}).get("direct_corridors", 0))
        independent_bus_units = min(2, direct_corridors)
        independent_units += independent_bus_units
        fallback_examples: list[dict[str, object]] = []
        if has_bus and has_rail:
            fallback_examples.append(
                {
                    "kind": "different_access_mode",
                    "label": "Use bus access instead of rail access",
                    "detail": "A practical bus entry and a practical rail entry were both confirmed.",
                }
            )
        for entry in practical_entries:
            station_id = entry.get("physical_station_id")
            if station_id and station_id != primary_id:
                fallback_examples.append(
                    {
                        "kind": "alternative_station",
                        "label": f"Enter rail at {entry.get('station_name', station_id)}",
                        "detail": "A second practical MRT entry station was confirmed.",
                        "station_name": entry.get("station_name", station_id),
                        "lines": entry.get("direct_lines", []),
                    }
                )
        for line in sorted(alternative_lines):
            fallback_examples.append(
                {
                    "kind": "alternative_rail_line",
                    "label": f"Use the {line} rail line",
                    "detail": "A practical alternative rail line was confirmed from another entry option.",
                    "line": line,
                }
            )
        for corridor in bus_coverage_result.evidence[:independent_bus_units]:
            services = corridor.get("member_services", [])
            service_labels = ", ".join(
                str(service.get("service"))
                for service in services
                if service.get("service")
            )
            fallback_examples.append(
                {
                    "kind": "independent_bus_corridor",
                    "label": f"Use bus corridor {service_labels or corridor.get('corridor_id', 'available')}",
                    "detail": corridor.get("destination") or "A distinct direct bus corridor was confirmed.",
                    "corridor_id": corridor.get("corridor_id"),
                }
            )
        evidence = {
            "access_modes": sorted(mode for mode in access_modes if mode),
            "independent_second_access_mode": has_bus and has_rail,
            "primary_physical_station_id": primary_id,
            "independent_second_physical_station": bool(alternative_stations),
            "alternative_rail_lines": sorted(alternative_lines),
            "independent_bus_corridors": independent_bus_units,
            "direct_corridor_count": direct_corridors,
            "fallback_examples": fallback_examples,
        }

    if independent_units <= 0:
        return ComponentResult(
            name="route_resilience",
            value={"independent_units": 0, "fallback_examples": [], "evidence": evidence},
            score=15.0,
            weight=weight,
            status=ComponentStatus.CALCULATED,
            explanation="No genuinely independent public-transport fallback was confirmed.",
            limitations=["A disruption to the single available entry corridor may remove practical access."],
            evidence=[evidence],
            source="Access alternatives + bus corridor and rail structure",
            provenance=Provenance.CURATED_REFERENCE_DATA,
            confidence="medium",
        )

    score = round(min(100.0, 20.0 + independent_units * 16.0), 1)
    return ComponentResult(
        name="route_resilience",
        value={"independent_units": independent_units, **evidence},
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=f"{independent_units} independent public-transport fallback unit(s) were confirmed.",
        strengths=["Alternatives are counted by independence of mode, station, line or corridor."],
        limitations=["Independence is structural evidence, not a live disruption simulation."],
        evidence=[evidence],
        source="Access alternatives + bus corridor and rail structure",
        provenance=Provenance.CURATED_REFERENCE_DATA,
        confidence="medium",
    )
