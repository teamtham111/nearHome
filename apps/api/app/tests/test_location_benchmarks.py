"""Block identity, measurement extraction and benchmark comparison rules.

All block data here is synthetic test input; none of it is a real ranking.
"""

from __future__ import annotations

import math

import pytest

from app.engines.location_benchmarks.comparison import Cohort, badness, compare
from app.engines.location_benchmarks.measurements import MEASUREMENTS_BY_KEY, extract_measurements
from app.utils.hdb_block_identity import block_key_from_address, block_key_from_parts, streets_equivalent


def _cohort(values: list[float | None], *, lower_is_better: bool, statuses: list[str] | None = None, reference=None):
    statuses = statuses or ["measured"] * len(values)
    bad = [badness(v, s, lower_is_better) for v, s in zip(values, statuses, strict=True)]
    return bad, Cohort.build([b for b in bad if b is not None], reference or len(values))


# Block identity ---------------------------------------------------------------


def test_block_number_alone_is_not_the_identity() -> None:
    assert block_key_from_parts("1", "BEACH RD") != block_key_from_parts("1", "JLN BT MERAH")


def test_hdb_abbreviations_and_portal_spellings_share_one_block_key() -> None:
    hdb = block_key_from_parts("291", "BT BATOK EAST AVE 6")
    portal = block_key_from_address("Blk 291 Bukit Batok East Avenue 6, Singapore 650291")
    assert hdb == portal == "291|BUKIT BATOK EAST AVENUE 6"
    assert block_key_from_parts("58", "C'WEALTH DR") == block_key_from_address("58 Commonwealth Drive")
    assert block_key_from_parts("123A", "BEDOK NTH ST 3") == block_key_from_address("123A Bedok North Street 3")


def test_street_equivalence_requires_same_token_count_and_abbreviation_order() -> None:
    assert streets_equivalent("TELOK BLANGAH HTS", "TELOK BLANGAH HEIGHTS")
    assert not streets_equivalent("ANG MO KIO AVENUE 3", "ANG MO KIO AVENUE 10")
    assert not streets_equivalent("JURONG WEST STREET 91", "JURONG EAST STREET 91")


# Comparison maths ---------------------------------------------------------------


def test_better_than_counts_strictly_worse_blocks_for_lower_is_better() -> None:
    bad, cohort = _cohort([5.0, 10.0, 15.0, 20.0, 25.0], lower_is_better=True)
    result = compare(bad[1], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0)
    # Subject 10 min excluded; 15, 20, 25 are strictly worse out of 4 others.
    assert result.group_size == 4
    assert result.strictly_worse == 3
    assert result.better_than_pct == 75


def test_direction_is_reversed_for_higher_is_better() -> None:
    bad, cohort = _cohort([1.0, 2.0, 3.0, 4.0, 5.0], lower_is_better=False)
    result = compare(bad[3], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0)
    assert result.strictly_worse == 3  # 1, 2, 3 corridors are worse than 4
    assert result.better_than_pct == 75


def test_ties_are_not_counted_as_worse_and_share_one_result() -> None:
    bad, cohort = _cohort([3.0, 3.0, 3.0, 1.0], lower_is_better=False)
    results = [compare(bad[i], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0) for i in range(3)]
    assert {r.better_than_pct for r in results} == {33}
    assert all(r.strictly_worse == 1 and r.tied == 2 for r in results)


def test_bottom_result_is_zero_and_100_needs_every_other_block_strictly_worse() -> None:
    bad, cohort = _cohort([1.0, 2.0, 3.0], lower_is_better=True)
    assert compare(bad[2], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0).better_than_pct == 0
    assert compare(bad[0], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0).better_than_pct == 100
    tied_best, tied_cohort = _cohort([1.0, 1.0, 3.0], lower_is_better=True)
    assert (
        compare(tied_best[0], tied_cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0).better_than_pct
        == 50
    )


def test_percentages_are_floored_never_rounded_up() -> None:
    bad, cohort = _cohort([0.0] + [float(i) for i in range(1, 8)], lower_is_better=True)
    # 6 of 7 others are strictly worse: 85.7% shown as 85%.
    assert compare(bad[1], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0).better_than_pct == 85


def test_subject_exclusion_only_applies_to_members() -> None:
    bad, cohort = _cohort([1.0, 2.0, 3.0, 4.0], lower_is_better=True)
    member = compare(bad[0], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0)
    outsider = compare(
        badness(1.0, "measured", True), cohort, subject_in_cohort=False, min_group_size=1, min_coverage=0
    )
    assert member.group_size == 3 and member.better_than_pct == 100
    assert outsider.group_size == 4 and outsider.better_than_pct == 75
    assert outsider.tied == 1


def test_missing_measurements_never_join_the_group_or_score_as_zero() -> None:
    bad, cohort = _cohort(
        [10.0, None, 30.0, 40.0], lower_is_better=True, statuses=["measured", "unavailable", "measured", "measured"]
    )
    assert cohort.valid_count == 3
    assert cohort.coverage == 0.75
    missing = compare(bad[1], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0)
    assert missing.status == "unavailable" and missing.better_than_pct is None


def test_genuine_no_access_ranks_below_every_measured_value() -> None:
    bad, cohort = _cohort([None, 50.0, 5.0], lower_is_better=True, statuses=["worst", "measured", "measured"])
    assert bad[0] == math.inf
    assert compare(bad[0], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0).better_than_pct == 0
    assert compare(bad[1], cohort, subject_in_cohort=True, min_group_size=1, min_coverage=0).better_than_pct == 50


def test_small_groups_and_low_coverage_are_suppressed() -> None:
    bad, cohort = _cohort([1.0, 2.0, 3.0], lower_is_better=True)
    small = compare(bad[0], cohort, subject_in_cohort=True, min_group_size=30)
    assert small.status == "suppressed" and small.better_than_pct is None and small.group_size == 2
    bad, sparse = _cohort([float(i) for i in range(40)], lower_is_better=True, reference=100)
    low = compare(bad[0], sparse, subject_in_cohort=True, min_group_size=30, min_coverage=0.5)
    assert low.status == "suppressed" and "40%" in (low.reason or "")


def test_cohort_json_round_trip_preserves_worst_results() -> None:
    bad, cohort = _cohort([None, 2.0, 1.0], lower_is_better=True, statuses=["worst", "measured", "measured"])
    restored = Cohort.from_json(cohort.to_json())
    assert restored == cohort


# Measurement extraction ---------------------------------------------------------


def _component(name: str, status: str = "calculated", score: float | None = 50.0, value: dict | None = None) -> dict:
    return {"name": name, "status": status, "score": score, "value": value}


def test_access_extraction_distinguishes_no_path_from_failure() -> None:
    no_path = {"components": [_component("access", value={"selected_access_path": None})]}
    failed = {"components": [_component("access", status="provider_error", score=None)]}
    measured = {"components": [_component("access", value={"selected_access_path": {"generalised_access_cost": 7.5}})]}
    assert extract_measurements("public_transport", no_path)["pt_access_cost_minutes"]["status"] == "worst"
    assert extract_measurements("public_transport", failed)["pt_access_cost_minutes"]["status"] == "unavailable"
    assert extract_measurements("public_transport", measured)["pt_access_cost_minutes"]["value"] == 7.5


def test_bus_reach_uses_existing_corridor_counts_and_keeps_not_assessed_unavailable() -> None:
    payload = {
        "components": [
            _component("bus_coverage", value={"direct_corridors": 6, "one_transfer_new_corridors": 9}),
            _component(
                "mrt_reach",
                value={"zero_transfer_30": 10, "one_transfer_30_incremental": 20, "multi_transfer_30_incremental": 3},
            ),
        ]
    }
    measured = extract_measurements("public_transport", payload)
    assert measured["pt_direct_bus_corridors"]["value"] == 6
    assert measured["pt_one_transfer_bus_corridors"]["value"] == 9
    assert measured["pt_mrt_stations_within_30_min"]["value"] == 33
    not_assessed = {"components": [_component("bus_coverage", status="not_assessed", score=None)]}
    assert extract_measurements("public_transport", not_assessed)["pt_direct_bus_corridors"]["status"] == "unavailable"


def test_every_ranked_measurement_declares_a_direction() -> None:
    for definition in MEASUREMENTS_BY_KEY.values():
        assert definition.direction in ("lower", "higher")
        assert definition.comparison_phrase == "Better than"
    assert MEASUREMENTS_BY_KEY["pt_access_cost_minutes"].lower_is_better
    assert not MEASUREMENTS_BY_KEY["pt_direct_bus_corridors"].lower_is_better


def test_town_legend_parses_hdb_metadata() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from data_pipeline.ingest_hdb_blocks import parse_town_legend

    legend = parse_town_legend(
        "Legend: AMK - ANG MO KIO BB - BUKIT BATOK BD - BEDOK BH - BISHAN BM - BUKIT MERAH BP - BUKIT PANJANG "
        "BT - BUKIT TIMAH CCK - CHOA CHU KANG CL - CLEMENTI CT - CENTRAL AREA GL - GEYLANG HG - HOUGANG "
        "JE - JURONG EAST JW - JURONG WEST KWN - KALLANG/WHAMPOA MP - MARINE PARADE PG - PUNGGOL PRC - PASIR RIS "
        "QT - QUEENSTOWN SB - SEMBAWANG SGN - SERANGOON SK - SENGKANG TAP - TAMPINES TG - TENGAH TP - TOA PAYOH "
        "WL - WOODLANDS YS - YISHUN"
    )
    assert legend["KWN"] == "KALLANG/WHAMPOA"
    assert legend["YS"] == "YISHUN"
    assert len(legend) == 27


@pytest.mark.parametrize("value", [None, "x", True])
def test_non_numeric_values_are_unavailable(value: object) -> None:
    payload = {"components": [_component("route_resilience", value={"independent_units": value})]}
    assert extract_measurements("public_transport", payload)["pt_independent_fallbacks"]["status"] == "unavailable"


def test_saint_street_names_are_not_read_as_street() -> None:
    hdb = block_key_from_parts("10", "ST. GEORGE'S RD")
    assert hdb == block_key_from_address("Blk 10 St George's Road") == "10|SAINT GEORGES ROAD"
    assert block_key_from_parts("5", "TAMPINES ST 81") == "5|TAMPINES STREET 81"
