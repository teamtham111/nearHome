"""Engine unit tests."""

from datetime import date, time
from uuid import uuid4

import pytest

from app.adapters.base import TransactionRecord
from app.domain.enums import (
    DataStatus,
    DayType,
    JourneyMode,
    ListingGroup,
    MainTransportMode,
    PriorityType,
    RequirementMetric,
    RequirementOperator,
    RequirementStatus,
    ScoreStatus,
)
from app.domain.models import (
    BuyerProfile,
    ConfirmedListing,
    HardRequirement,
    JourneyEstimate,
    build_priorities,
)
from app.engines.fair_price import FairPriceEngine
from app.engines.fair_price_comparables import (
    ComparableConfig,
    _similarity_components,
    derive_lease_commencement_from_transactions,
    derive_town_from_transactions,
    derive_town_match_evidence,
    infer_flat_model_from_transactions,
    select_comparables,
)
from app.engines.immediate_comparison import ImmediateComparisonEngine
from app.engines.preference_scoring import PreferenceScoringEngine
from app.engines.recommendation import RecommendationEngine
from app.engines.requirement_engine import RequirementEngine, RequirementRegistryError


def _listing(price: float, area: float = 90.0, lease: float | None = None) -> ConfirmedListing:
    return ConfirmedListing(
        listing_id=uuid4(),
        session_id=uuid4(),
        display_name=f"Flat at {price}",
        asking_price=price,
        floor_area_sqm=area,
        address="123 Test Street",
        flat_type="4 ROOM",
        remaining_lease_years=lease,
    )


def _profile(budget: float = 700_000, priorities=None, reqs=None) -> BuyerProfile:
    return BuyerProfile(
        max_budget=budget,
        priorities=priorities or build_priorities([(PriorityType.AFFORDABILITY, None)]),
        main_transport_mode=MainTransportMode.MAINLY_PUBLIC_TRANSPORT,
        hard_requirements=reqs or [],
    )


class TestImmediateComparison:
    def test_budget_difference(self):
        listings = [_listing(680_000), _listing(720_000)]
        profile = _profile(budget=700_000)
        metrics = ImmediateComparisonEngine.compute(listings, profile)
        names = {m.listing_id: m for m in metrics if m.metric_name == "budget_difference"}
        assert names[listings[0].listing_id].raw_value == 20_000
        assert names[listings[1].listing_id].raw_value == -20_000
        assert not {m.metric_name for m in metrics} & {
            "committed_cost",
            "committed_budget_difference",
            "storey_band",
        }

    @pytest.mark.parametrize(
        ("maximum_budget", "asking_price", "expected"),
        [
            (700_000, 650_000, 50_000),
            (700_000, 730_000, -30_000),
            (700_000, 700_000, 0),
            (None, 700_000, None),
            (700_000, None, None),
        ],
    )
    def test_budget_difference_cases(self, maximum_budget, asking_price, expected):
        assert ImmediateComparisonEngine.budget_difference(maximum_budget, asking_price) == expected

    def test_price_per_sqm(self):
        listings = [_listing(630_000, area=90)]
        metrics = ImmediateComparisonEngine.compute(listings, None)
        ppsm = next(m for m in metrics if m.metric_name == "price_per_sqm")
        assert ppsm.raw_value == pytest.approx(7000.0, rel=0.01)


class TestRequirementEngine:
    def test_rejects_journey_requirement(self):
        req = HardRequirement(
            requirement_id=None,
            metric="journey_duration_seconds",  # type: ignore[arg-type]
            operator=RequirementOperator.LTE,
            threshold=30,
        )
        with pytest.raises(RequirementRegistryError):
            RequirementEngine.validate_requirement(req)

    def test_journey_requirement_passes_and_fails_without_affecting_driving_score(self):
        location_id = uuid4()
        listings = [_listing(680_000), _listing(690_000)]
        profile = _profile(
            reqs=[
                HardRequirement(
                    requirement_id=None,
                    metric=RequirementMetric.MAX_DRIVING_JOURNEY_MINUTES,
                    operator=RequirementOperator.LTE,
                    threshold=30,
                    important_location_id=location_id,
                )
            ]
        )
        journeys = [
            JourneyEstimate(
                uuid4(),
                listings[0].listing_id,
                location_id,
                JourneyMode.DRIVING,
                DayType.WEEKDAY,
                time(8),
                "Asia/Singapore",
                None,
                24 * 60,
                0,
                True,
                DataStatus.AVAILABLE,
                "TEST",
                "OK",
                None,
            ),
            JourneyEstimate(
                uuid4(),
                listings[1].listing_id,
                location_id,
                JourneyMode.DRIVING,
                DayType.WEEKDAY,
                time(8),
                "Asia/Singapore",
                None,
                35 * 60,
                0,
                True,
                DataStatus.AVAILABLE,
                "TEST",
                "OK",
                None,
            ),
        ]
        results = RequirementEngine.evaluate_all(listings, profile, [], journeys)
        assert [result.status for result in results] == [RequirementStatus.PASS, RequirementStatus.FAIL]

    def test_journey_requirement_cannot_determine_when_routing_is_unavailable(self):
        location_id = uuid4()
        listing = _listing(680_000)
        profile = _profile(
            reqs=[
                HardRequirement(
                    requirement_id=None,
                    metric=RequirementMetric.MAX_DRIVING_JOURNEY_MINUTES,
                    operator=RequirementOperator.LTE,
                    threshold=30,
                    important_location_id=location_id,
                )
            ]
        )
        journey = JourneyEstimate(
            uuid4(),
            listing.listing_id,
            location_id,
            JourneyMode.DRIVING,
            DayType.WEEKDAY,
            time(8),
            "Asia/Singapore",
            None,
            None,
            None,
            None,
            DataStatus.TEMPORARILY_UNAVAILABLE,
            "TEST",
            "ERROR",
            None,
        )
        result = RequirementEngine.evaluate_all([listing], profile, [], [journey])[0]
        assert result.status == RequirementStatus.CANNOT_DETERMINE

    def test_missing_lease_is_cannot_determine(self):
        listing = _listing(680_000, lease=None)
        profile = _profile(
            reqs=[
                HardRequirement(
                    requirement_id=None,
                    metric=RequirementMetric.REMAINING_LEASE_YEARS,
                    operator=RequirementOperator.GTE,
                    threshold=70,
                )
            ]
        )
        metrics = ImmediateComparisonEngine.compute([listing], profile)
        results = RequirementEngine.evaluate_all([listing], profile, metrics)
        assert results[0].status == RequirementStatus.CANNOT_DETERMINE

    def test_lease_requirement_compares_canonical_months_not_rounded_years(self):
        listing = _listing(680_000, lease=779 / 12)
        listing.remaining_lease_months = 779
        profile = _profile(
            reqs=[
                HardRequirement(
                    requirement_id=None,
                    metric=RequirementMetric.REMAINING_LEASE_YEARS,
                    operator=RequirementOperator.GTE,
                    threshold=65,
                )
            ]
        )
        results = RequirementEngine.evaluate_all(
            [listing], profile, ImmediateComparisonEngine.compute([listing], profile)
        )
        assert results[0].status == RequirementStatus.FAIL


class TestRecommendation:
    def test_failing_requirement_cannot_win(self):
        cheap = _listing(650_000, area=90, lease=80)
        expensive = _listing(680_000, area=100, lease=80)
        profile = _profile(
            priorities=build_priorities([(PriorityType.AFFORDABILITY, None)]),
            reqs=[
                HardRequirement(
                    requirement_id=None,
                    metric=RequirementMetric.FLOOR_AREA_SQM,
                    operator=RequirementOperator.LTE,
                    threshold=95,
                )
            ],
        )
        listings = [cheap, expensive]
        metrics = ImmediateComparisonEngine.compute(listings, profile)
        reqs = RequirementEngine.evaluate_all(listings, profile, metrics)
        groups = RequirementEngine.classify_listings([l.listing_id for l in listings], reqs)
        eligible = RequirementEngine.eligible_listing_ids([l.listing_id for l in listings], groups)
        scores = PreferenceScoringEngine.score(listings, eligible, profile, metrics)
        rec, _ = RecommendationEngine.recommend(uuid4(), listings, profile, reqs, scores, "hash")
        assert rec.recommended_listing_id == cheap.listing_id
        assert expensive.listing_id not in eligible or groups[expensive.listing_id] != ListingGroup.PASSES_ALL


class TestSchoolsEngine:
    def test_nearby_schools(self):
        from app.engines.schools import SchoolsEngine

        result = SchoolsEngine.compute(uuid4(), 1.3521, 103.8498)
        assert len(result.nearby_schools) >= 1

    def test_multiple_named_schools_return_separate_distances(self, monkeypatch):
        from app.adapters.reference_data import ReferenceDataStore, School
        from app.engines.schools import SchoolsEngine

        schools = [
            School("Raffles Institution", "SECONDARY", 1.0, 103.0, "A"),
            School("Nanyang Primary School", "PRIMARY", 1.1, 103.0, "B"),
        ]
        monkeypatch.setattr(ReferenceDataStore, "schools", classmethod(lambda cls: schools))
        monkeypatch.setattr(
            ReferenceDataStore,
            "nearby_schools",
            classmethod(lambda cls, *_args, **_kwargs: [(schools[0], 0.5), (schools[1], 1.2)]),
        )

        result = SchoolsEngine.compute(
            uuid4(),
            1.0,
            103.0,
            named_schools=["Raffles Institution", "Nanyang Primary School"],
        )

        assert result.named_school_distances_km == {
            "Raffles Institution": 0.5,
            "Nanyang Primary School": 1.2,
        }

    def test_named_school_matching_normalises_case_whitespace_punctuation_and_suffixes(self, monkeypatch):
        from app.adapters.reference_data import ReferenceDataStore, School
        from app.engines.schools import SchoolsEngine

        schools = [School("Nanyang Primary School", "PRIMARY", 1.0, 103.0, "A")]
        monkeypatch.setattr(ReferenceDataStore, "schools", classmethod(lambda cls: schools))
        monkeypatch.setattr(ReferenceDataStore, "nearby_schools", classmethod(lambda cls, *_args, **_kwargs: []))

        result = SchoolsEngine.compute(uuid4(), 1.0, 103.0, named_schools=["  nanyang-primary  "])

        assert result.matched_named_schools == {"nanyang-primary": "Nanyang Primary School"}
        assert result.named_school_distances_km["nanyang-primary"] == 0.0

    def test_named_school_match_does_not_choose_ambiguous_suffix_match(self, monkeypatch):
        from app.adapters.reference_data import ReferenceDataStore, School
        from app.engines.schools import SchoolsEngine

        schools = [
            School("Example Primary School", "PRIMARY", 1.0, 103.0, "A"),
            School("Example Secondary School", "SECONDARY", 1.1, 103.0, "B"),
        ]
        monkeypatch.setattr(ReferenceDataStore, "schools", classmethod(lambda cls: schools))
        monkeypatch.setattr(ReferenceDataStore, "nearby_schools", classmethod(lambda cls, *_args, **_kwargs: []))

        result = SchoolsEngine.compute(uuid4(), 1.0, 103.0, named_schools=["Example School"])

        assert result.matched_named_schools == {"Example School": None}
        assert result.named_school_distances_km == {"Example School": None}


class TestDimensionScores:
    # Public Transport / Driving engine-level scoring behaviour (component
    # scoring, not_assessed handling, recommendation gating) is covered by
    # the dedicated test_public_transport_engine.py / test_driving_engine.py
    # suites, since those engines now require a RoutingProvider and live
    # reference-data fixtures rather than the old coordinate-only heuristics.

    def test_school_boundaries_and_duplicates(self, monkeypatch):
        from app.adapters.reference_data import ReferenceDataStore, School
        from app.engines.schools import SchoolsEngine

        schools = [
            School("Boundary Primary", "PRIMARY", 1.0, 103.0, "A"),
            School("Boundary Primary", "PRIMARY", 1.0, 103.0, "A"),
            School("Two Km Secondary", "SECONDARY", 1.0, 103.0, "B"),
        ]
        monkeypatch.setattr(ReferenceDataStore, "schools", classmethod(lambda cls: schools))
        monkeypatch.setattr(
            ReferenceDataStore,
            "nearby_schools",
            classmethod(lambda cls, *_args, **_kwargs: [(schools[0], 1.0), (schools[1], 1.0), (schools[2], 2.0)]),
        )

        result = SchoolsEngine.compute(uuid4(), 1.0, 103.0)

        assert result.status == DataStatus.AVAILABLE
        assert result.schools_within_1km == 1
        assert result.schools_within_2km == 2
        assert result.score_status == ScoreStatus.CALCULATED

    def test_missing_dimension_score_is_not_zero(self):
        from app.engines.schools import SchoolsEngine

        result = SchoolsEngine.compute(uuid4(), None, None)

        assert result.score is None
        assert result.score_status == ScoreStatus.MISSING_INPUT

    def test_preference_score_uses_transport_data_and_avoids_nan(self):
        listings = [_listing(650_000), _listing(680_000)]
        profile = _profile(
            priorities=build_priorities([(PriorityType.PUBLIC_TRANSPORT, None)]),
        )
        fields = {
            listings[0].listing_id: {"public_transport": {"overall_score": 90}},
            listings[1].listing_id: {"public_transport": {"overall_score": 60}},
        }

        scores = PreferenceScoringEngine.score(
            listings,
            [l.listing_id for l in listings],
            profile,
            [],
            enriched_fields_by_listing=fields,
        )

        assert scores[0].total_score > scores[1].total_score
        assert all(score.total_score == score.total_score for score in scores)

        recommendation, _trace = RecommendationEngine.recommend(
            uuid4(),
            listings,
            profile,
            [],
            scores,
            "transport-test",
        )
        assert recommendation.recommended_listing_id == listings[0].listing_id

    def test_absolute_fit_scores_are_not_shortlist_relative_winner_values(self):
        bishan = _listing(650_000)
        bishan.address = "217 Bishan Street 23"
        whampoa = _listing(690_000)
        whampoa.address = "72 Whampoa Drive"
        profile = _profile(
            priorities=build_priorities(
                [(PriorityType.AFFORDABILITY, None), (PriorityType.PUBLIC_TRANSPORT, None)]
            ),
        )
        fields = {
            bishan.listing_id: {"public_transport": {"overall_score": 82}},
            whampoa.listing_id: {"public_transport": {"overall_score": 75}},
        }

        scores = PreferenceScoringEngine.score(
            [bishan, whampoa], [bishan.listing_id, whampoa.listing_id], profile, [], enriched_fields_by_listing=fields
        )

        by_listing = {score.listing_id: score for score in scores}
        assert by_listing[bishan.listing_id].overall_fit_score == pytest.approx(72.04)
        assert by_listing[whampoa.listing_id].overall_fit_score == pytest.approx(62.55)
        assert all(1 < score.overall_fit_score < 100 for score in scores if score.overall_fit_score is not None)
        assert all(score.total_score == score.overall_fit_score for score in scores)
        assert {score.rank for score in scores} == {1, 2}

    def test_clear_winner_does_not_reduce_other_listing_to_zero(self):
        stronger = _listing(600_000)
        weaker = _listing(780_000)
        profile = _profile(
            priorities=build_priorities(
                [(PriorityType.AFFORDABILITY, None), (PriorityType.PUBLIC_TRANSPORT, None)]
            ),
        )
        fields = {
            stronger.listing_id: {"public_transport": {"overall_score": 95}},
            weaker.listing_id: {"public_transport": {"overall_score": 45}},
        }

        scores = PreferenceScoringEngine.score(
            [stronger, weaker], [stronger.listing_id, weaker.listing_id], profile, [], enriched_fields_by_listing=fields
        )

        assert scores[0].overall_fit_score and scores[1].overall_fit_score
        assert scores[0].overall_fit_score > scores[1].overall_fit_score > 0

    def test_missing_transport_is_excluded_and_remaining_weight_is_renormalised(self):
        listings = [_listing(650_000), _listing(650_000)]
        profile = _profile(
            priorities=build_priorities(
                [(PriorityType.AFFORDABILITY, None), (PriorityType.PUBLIC_TRANSPORT, None)]
            ),
        )
        fields = {listings[0].listing_id: {"public_transport": {"overall_score": 80}}}

        scores = PreferenceScoringEngine.score(
            listings, [listing.listing_id for listing in listings], profile, [], enriched_fields_by_listing=fields
        )
        missing_transport = next(score for score in scores if score.listing_id == listings[1].listing_id)

        assert missing_transport.coverage == 0.5
        assert missing_transport.weights == {"AFFORDABILITY": 1.0}
        assert missing_transport.overall_fit_score == pytest.approx(64.29)

    def test_requirement_failure_does_not_remove_preference_fit_score(self):
        failing = _listing(650_000, area=80)
        passing = _listing(680_000, area=100)
        profile = _profile(
            priorities=build_priorities([(PriorityType.AFFORDABILITY, None)]),
            reqs=[
                HardRequirement(
                    requirement_id=None,
                    metric=RequirementMetric.FLOOR_AREA_SQM,
                    operator=RequirementOperator.GTE,
                    threshold=90,
                )
            ],
        )
        listings = [failing, passing]
        metrics = ImmediateComparisonEngine.compute(listings, profile)
        requirements = RequirementEngine.evaluate_all(listings, profile, metrics)
        scores = PreferenceScoringEngine.score(
            listings, [listing.listing_id for listing in listings], profile, metrics
        )
        recommendation, _ = RecommendationEngine.recommend(
            uuid4(), listings, profile, requirements, scores, "requirement-fit"
        )

        failing_score = next(score for score in scores if score.listing_id == failing.listing_id)
        assert failing_score.overall_fit_score is not None
        assert recommendation.recommended_listing_id == passing.listing_id

    def test_equal_absolute_component_scores_remain_equal_after_ranking(self):
        listings = [_listing(650_000), _listing(650_000)]
        profile = _profile(priorities=build_priorities([(PriorityType.PUBLIC_TRANSPORT, None)]))
        fields = {listing.listing_id: {"public_transport": {"overall_score": 80}} for listing in listings}

        scores = PreferenceScoringEngine.score(
            listings, [listing.listing_id for listing in listings], profile, [], enriched_fields_by_listing=fields
        )

        assert [score.overall_fit_score for score in scores] == [80.0, 80.0]
        assert [score.rank for score in scores] == [1, 1]

    def test_enrichment_adds_absolute_component_without_replacing_fit_with_rank(self):
        listing = _listing(650_000)
        profile = _profile(
            priorities=build_priorities(
                [(PriorityType.AFFORDABILITY, None), (PriorityType.PUBLIC_TRANSPORT, None)]
            ),
        )

        before = PreferenceScoringEngine.score([listing], [listing.listing_id], profile, [])
        after = PreferenceScoringEngine.score(
            [listing], [listing.listing_id], profile, [],
            enriched_fields_by_listing={listing.listing_id: {"public_transport": {"overall_score": 80}}},
        )

        assert before[0].overall_fit_score == pytest.approx(64.29)
        assert after[0].overall_fit_score == pytest.approx(71.16)
        assert after[0].overall_fit_score not in {0, 1}

    def test_unassessed_fit_is_not_ranked_as_zero(self):
        listings = [_listing(650_000), _listing(680_000)]
        profile = _profile(priorities=build_priorities([(PriorityType.PUBLIC_TRANSPORT, None)]))

        scores = PreferenceScoringEngine.score(
            listings, [listing.listing_id for listing in listings], profile, []
        )
        recommendation, _ = RecommendationEngine.recommend(
            uuid4(), listings, profile, [], scores, "unassessed-fit"
        )

        assert all(score.overall_fit_score is None for score in scores)
        assert recommendation.recommended_listing_id is None
        assert recommendation.reason_codes == ["NO_ASSESSED_PRIORITY_DATA"]

    def test_recommendation_explanation_does_not_expose_fit_point_difference(self):
        listings = [_listing(600_000), _listing(780_000)]
        profile = _profile(priorities=build_priorities([(PriorityType.AFFORDABILITY, None)]))
        scores = PreferenceScoringEngine.score(
            listings, [listing.listing_id for listing in listings], profile, []
        )

        recommendation, _ = RecommendationEngine.recommend(
            uuid4(), listings, profile, [], scores, "plain-language-why-not"
        )

        explanation = recommendation.why_not_selected[str(listings[1].listing_id)]
        assert explanation == "Another eligible listing was recommended based on your saved preferences."
        assert "point" not in explanation.lower()
        assert "lower overall fit" not in explanation.lower()

    def test_missing_priority_data_is_renormalised(self):
        listings = [_listing(650_000), _listing(680_000)]
        profile = _profile(
            priorities=build_priorities([(PriorityType.PUBLIC_TRANSPORT, None), (PriorityType.SCHOOLS, None)]),
        )
        fields = {listing.listing_id: {"public_transport": {"overall_score": 80}} for listing in listings}

        scores = PreferenceScoringEngine.score(
            listings,
            [l.listing_id for l in listings],
            profile,
            [],
            enriched_fields_by_listing=fields,
        )

        assert all(set(score.weights) == {"PUBLIC_TRANSPORT"} for score in scores)
        assert all(score.coverage == 0.5 for score in scores)

    def test_driving_priority_uses_general_score_not_destination_journey(self):
        listings = [_listing(650_000), _listing(680_000)]
        location_id = uuid4()
        profile = _profile(priorities=build_priorities([(PriorityType.DRIVING, None)]))
        fields = {
            listings[0].listing_id: {"driving_access": {"overall_score": 90}},
            listings[1].listing_id: {"driving_access": {"overall_score": 60}},
        }
        journeys = [
            JourneyEstimate(
                uuid4(),
                listings[0].listing_id,
                location_id,
                JourneyMode.DRIVING,
                DayType.WEEKDAY,
                time(8),
                "Asia/Singapore",
                None,
                60 * 60,
                0,
                True,
                DataStatus.AVAILABLE,
                "TEST",
                "OK",
                None,
            ),
            JourneyEstimate(
                uuid4(),
                listings[1].listing_id,
                location_id,
                JourneyMode.DRIVING,
                DayType.WEEKDAY,
                time(8),
                "Asia/Singapore",
                None,
                20 * 60,
                0,
                True,
                DataStatus.AVAILABLE,
                "TEST",
                "OK",
                None,
            ),
        ]
        scores = PreferenceScoringEngine.score(
            listings, [l.listing_id for l in listings], profile, [], journeys, fields
        )
        assert scores[0].raw_values["DRIVING"] == 90
        assert scores[1].raw_values["DRIVING"] == 60

    def test_destination_priority_uses_journey_not_general_driving_score(self):
        listings = [_listing(650_000), _listing(680_000)]
        location_id = uuid4()
        profile = _profile(priorities=build_priorities([(PriorityType.IMPORTANT_LOCATION_JOURNEY, location_id)]))
        fields = {
            listings[0].listing_id: {"driving_access": {"overall_score": 60}},
            listings[1].listing_id: {"driving_access": {"overall_score": 90}},
        }
        journeys = [
            JourneyEstimate(
                uuid4(),
                listings[0].listing_id,
                location_id,
                JourneyMode.DRIVING,
                DayType.WEEKDAY,
                time(8),
                "Asia/Singapore",
                None,
                20 * 60,
                0,
                True,
                DataStatus.AVAILABLE,
                "TEST",
                "OK",
                None,
            ),
            JourneyEstimate(
                uuid4(),
                listings[1].listing_id,
                location_id,
                JourneyMode.DRIVING,
                DayType.WEEKDAY,
                time(8),
                "Asia/Singapore",
                None,
                60 * 60,
                0,
                True,
                DataStatus.AVAILABLE,
                "TEST",
                "OK",
                None,
            ),
        ]
        scores = PreferenceScoringEngine.score(
            listings, [l.listing_id for l in listings], profile, [], journeys, fields
        )
        assert scores[0].raw_values[f"important_location_journey:{location_id}"] == 20 * 60
        assert scores[1].raw_values[f"important_location_journey:{location_id}"] == 60 * 60

    def test_fair_price_priority_uses_value_gap_and_missing_data_is_not_zero(self):
        listings = [_listing(600_000), _listing(700_000), _listing(650_000)]
        profile = _profile(priorities=build_priorities([(PriorityType.FAIR_PRICE, None)]))
        fields = {
            listings[0].listing_id: {
                "fair_price": {"status": "AVAILABLE", "final_estimate": 700_000, "confidence": "HIGH"}
            },
            listings[1].listing_id: {
                "fair_price": {"status": "AVAILABLE", "final_estimate": 700_000, "confidence": "HIGH"}
            },
            listings[2].listing_id: {"fair_price": {"status": "INSUFFICIENT_EVIDENCE", "final_estimate": None}},
        }
        scores = PreferenceScoringEngine.score(
            listings, [listing.listing_id for listing in listings], profile, [], enriched_fields_by_listing=fields
        )
        raw = {score.listing_id: score.raw_values["FAIR_PRICE"] for score in scores}
        assert raw[listings[0].listing_id] == pytest.approx(100_000 / 700_000)
        assert raw[listings[1].listing_id] == pytest.approx(0.0)
        assert raw[listings[2].listing_id] is None
        assert scores[0].coverage == pytest.approx(1.0)


def _record(month: str, price: float, *, block: str = "123", lease: float = 65.0) -> TransactionRecord:
    return TransactionRecord(
        transaction_id=f"{month}-{price}",
        transaction_month=month,
        town="BISHAN",
        flat_type="4 ROOM",
        block=block,
        street="BISHAN STREET 12",
        storey_range="04 TO 06",
        floor_area_sqm=90.0,
        flat_model="Model A",
        lease_commencement=1990,
        remaining_lease=lease,
        resale_price=price,
        price_per_sqm=price / 90.0,
    )


class TestFairPriceValuation:
    def test_subtype_is_not_an_independent_similarity_feature(self):
        listing = _listing(650_000)
        listing.raw_listing_subtype = "4A"
        listing.flat_model = "Model A"
        components = _similarity_components(
            _record("2025-01", 650_000),
            listing,
            target_town="BISHAN",
            target_block="123",
            target_street="BISHAN STREET 12",
            target_lease=780,
            decay_months=36,
            valuation_date=date(2025, 6, 1),
        )

        assert "flat_model" in components
        assert "listing_flat_subtype" not in components

    @pytest.mark.parametrize(
        ("listing_address", "block", "street", "town"),
        [
            ("183 Jelebu Road 670183", "183", "JELEBU RD", "BUKIT PANJANG"),
            ("805 King George’s Avenue", "805", "KING GEORGE'S AVE", "KALLANG/WHAMPOA"),
            ("406 Sin Ming Avenue", "406", "SIN MING AVE", "BISHAN"),
            ("211 Jurong East Street 21", "211", "JURONG EAST ST 21", "JURONG EAST"),
            ("337 Jurong East Avenue 1", "337", "JURONG EAST AVE 1", "JURONG EAST"),
            ("637 Veerasamy Road", "637", "VEERASAMY RD", "CENTRAL AREA"),
        ],
    )
    def test_town_derivation_matches_canonical_hdb_address_variants(self, listing_address, block, street, town):
        records = [
            TransactionRecord(
                transaction_id="address-match",
                transaction_month="2025-01",
                town=town,
                flat_type="4 ROOM",
                block=block,
                street=street,
                storey_range="01 TO 03",
                floor_area_sqm=90.0,
                flat_model="Model A",
                lease_commencement=1990,
                remaining_lease=63.0,
                resale_price=500_000,
                price_per_sqm=5555.0,
                remaining_lease_months=756,
            )
        ]
        assert derive_town_from_transactions(records, listing_address) == (
            town,
            "historical_transaction_match",
        )

    def test_town_derivation_does_not_match_different_block_or_similar_street(self):
        record = TransactionRecord(
            transaction_id="exact-only",
            transaction_month="2025-01",
            town="BUKIT PANJANG",
            flat_type="4 ROOM",
            block="183",
            street="JELEBU RD",
            storey_range="01 TO 03",
            floor_area_sqm=90.0,
            flat_model="Model A",
            lease_commencement=1990,
            remaining_lease=63.0,
            resale_price=500_000,
            price_per_sqm=5555.0,
            remaining_lease_months=756,
        )
        assert derive_town_from_transactions([record], "184 Jelebu Road") == (None, None)
        assert derive_town_from_transactions([record], "183 Jelebu Road 2") == (None, None)

    def test_ambiguous_canonical_address_is_not_assigned_arbitrarily(self):
        records = []
        for town in ("BISHAN", "TOA PAYOH"):
            records.append(
                TransactionRecord(
                    transaction_id=town,
                    transaction_month="2025-01",
                    town=town,
                    flat_type="4 ROOM",
                    block="406",
                    street="SIN MING AVE",
                    storey_range="01 TO 03",
                    floor_area_sqm=90.0,
                    flat_model="Model A",
                    lease_commencement=1990,
                    remaining_lease=63.0,
                    resale_price=500_000,
                    price_per_sqm=5555.0,
                    remaining_lease_months=756,
                )
            )
        assert derive_town_from_transactions(records, "406 Sin Ming Avenue") == (None, None)
        evidence = derive_town_match_evidence(records, "406 Sin Ming Avenue")
        assert evidence["reason"] == "ambiguous_town_for_canonical_address"
        assert evidence["matched_towns"] == ["BISHAN", "TOA PAYOH"]

    def test_filter_status_reflects_optional_missing_fields_without_contradiction(self):
        listing = _listing(600_000, area=90.0, lease=65.0)
        listing.address = "123 Bishan Street 12"
        listing.flat_model = None
        listing.storey_range = None
        records = [_record(f"2024-{month:02d}", 550_000 + month * 1_000) for month in range(1, 9)]

        selection = select_comparables(records, listing, "BISHAN", date(2025, 3, 1))

        assert selection is not None
        assert selection.filter_status["town"]["status"] == "applied"
        assert selection.filter_status["flat_type"]["status"] == "applied"
        assert selection.filter_status["flat_model"]["status"] == "omitted_missing"
        assert selection.filter_status["storey_range"]["status"] == "omitted_missing"
        assert any("Storey was not provided" in message for message in selection.filter_messages)
        assert not any("No relaxation; same town" in message for message in selection.filter_messages)

    def test_missing_town_is_recorded_and_not_described_as_same_town_matching(self):
        listing = _listing(600_000, area=90.0, lease=65.0)
        records = [_record(f"2024-{month:02d}", 550_000 + month * 1_000) for month in range(1, 5)]

        selection = select_comparables(records, listing, None, date(2025, 3, 1))

        assert selection is not None
        assert selection.filter_status["town"]["status"] == "omitted_missing"
        assert any("town matching was omitted" in message for message in selection.filter_messages)
        assert not any("same town" in message.lower() for message in selection.filter_messages)

    def test_town_and_unambiguous_flat_model_can_be_derived_from_transactions(self):
        listing = _listing(600_000, area=90.0, lease=65.0)
        listing.address = "123 Bishan Street 12"
        records = [_record(f"2024-{month:02d}", 550_000, block="123") for month in range(1, 4)]

        town, town_source = derive_town_from_transactions(records, listing.address)
        model, model_source = infer_flat_model_from_transactions(records, listing)

        assert (town, town_source) == ("BISHAN", "historical_transaction_match")
        assert (model, model_source) == ("Model A", "historical_transactions")

    def test_lease_commencement_can_be_derived_only_from_exact_unambiguous_address(self):
        records = [_record(f"2024-{month:02d}", 550_000, block="123") for month in range(1, 4)]

        commencement, source = derive_lease_commencement_from_transactions(
            records,
            "123 Bishan Street 12",
        )

        assert (commencement, source) == (1990, "historical_transactions")

    def test_lease_commencement_is_not_guessed_when_address_has_conflicting_records(self):
        records = [_record("2024-01", 550_000, block="123")]
        conflicting = _record("2024-02", 551_000, block="123")
        conflicting.lease_commencement = 1991
        records.append(conflicting)

        commencement, source = derive_lease_commencement_from_transactions(
            records,
            "123 Bishan Street 12",
        )

        assert commencement is None
        assert source is None

    def test_lease_is_used_by_comparable_selection_without_defaulting(self):
        listing = _listing(600_000, area=90.0, lease=72.5)
        records = [_record(f"2024-{month:02d}", 550_000, lease=72.5) for month in range(1, 5)]
        selection = select_comparables(records, listing, "BISHAN", date(2025, 3, 1))
        assert selection is not None
        assert selection.eligible_comparable_count == 4
        assert all(abs(row["remaining_lease"] - 72.5) < 0.01 for row in selection.rows)

    def test_recency_similarity_is_continuous_and_monotonic(self):
        listing = _listing(600_000, area=90.0, lease=65.0)
        recent = _record("2025-02", 550_000)
        older = _record("2024-02", 550_000)
        recent_parts = _similarity_components(
            recent, listing, "BISHAN", "123", "BISHAN STREET 12", 65.0, 12.0, date(2025, 3, 1)
        )
        older_parts = _similarity_components(
            older, listing, "BISHAN", "123", "BISHAN STREET 12", 65.0, 12.0, date(2025, 3, 1)
        )
        assert 0 < older_parts["recency"] < recent_parts["recency"] < 1

    def test_missing_lease_never_receives_an_implicit_default(self):
        listing = _listing(600_000, area=90.0, lease=None)
        result = FairPriceEngine.estimate(listing, "BISHAN", records=[])
        assert result.status == DataStatus.INSUFFICIENT_EVIDENCE

    def test_zero_lease_is_treated_as_missing(self):
        listing = _listing(600_000, area=90.0, lease=0)
        result = FairPriceEngine.estimate(listing, "BISHAN")
        assert result.status == DataStatus.INSUFFICIENT_EVIDENCE

    def test_equivalent_hdb_flat_type_labels_match_transactions(self):
        listing = ConfirmedListing(
            listing_id=uuid4(),
            session_id=uuid4(),
            display_name="Pasir Ris flat",
            asking_price=738_000,
            floor_area_sqm=127.0,
            address="745 Pasir Ris Street 71",
            flat_type="5 ROOM HDB",
            remaining_lease_years=67.0,
        )
        result = FairPriceEngine.estimate(listing, "PASIR RIS")
        assert result.status == DataStatus.AVAILABLE
        assert result.method == "WEIGHTED_COMPARABLES_FALLBACK"
        assert len(result.comparables) >= 3

    def test_future_transactions_are_excluded_and_asking_price_is_not_a_feature(self):
        records = [
            _record("2024-01", 540_000),
            _record("2024-06", 550_000),
            _record("2024-12", 560_000),
            _record("2025-03", 999_999),
        ]
        listing = _listing(600_000, area=90.0, lease=65.0)
        selection = select_comparables(records, listing, "BISHAN", date(2025, 3, 1))
        assert selection is not None
        assert selection.eligible_comparable_count == 3
        assert all(row["transaction_date"] < "2025-03" for row in selection.rows)

        low_asking = FairPriceEngine.estimate(listing, "BISHAN", valuation_date=date(2025, 3, 1), records=records)
        high_asking = FairPriceEngine.estimate(
            _listing(900_000, area=90.0, lease=65.0),
            "BISHAN",
            valuation_date=date(2025, 3, 1),
            records=records,
        )
        assert low_asking.central_estimate == high_asking.central_estimate
        assert low_asking.value_gap_percentage != high_asking.value_gap_percentage

    def test_comparable_selection_returns_strongest_rows_and_records_relaxation(self):
        records = [
            _record(f"2024-{month:02d}", 500_000 + month * 1_000, block=str(100 + month)) for month in range(1, 13)
        ]
        listing = _listing(600_000, area=90.0, lease=65.0)
        selection = select_comparables(
            records,
            listing,
            "BISHAN",
            date(2025, 1, 1),
            ComparableConfig(target_comparables=20, strongest_count=5),
        )
        assert selection is not None
        assert len(selection.rows) == 5
        assert selection.eligible_comparable_count == 12
        assert selection.relaxation_level >= 0
        assert all("similarity_components" in row for row in selection.rows)

    def test_contextual_comparables_are_capped_at_ten_without_changing_eligible_count(self):
        records = [_record(f"2024-{month:02d}", 500_000 + month * 1_000) for month in range(1, 13)]
        listing = _listing(600_000, area=90.0, lease=65.0)
        selection = select_comparables(records, listing, "BISHAN", date(2025, 3, 1))
        assert selection is not None
        assert selection.eligible_comparable_count == 12
        assert len(selection.all_rows) == 12
        assert len(selection.rows) == 10
        assert [row["transaction_date"] for row in selection.rows] == [
            f"2024-{month:02d}" for month in range(12, 2, -1)
        ]

    def test_duplicate_transaction_ids_are_removed_before_selection(self):
        records = [_record(f"2024-{month:02d}", 500_000 + month * 1_000) for month in range(1, 13)]
        records.append(records[0])
        selection = select_comparables(records, _listing(600_000, area=90.0, lease=65.0), "BISHAN", date(2025, 3, 1))
        assert selection is not None
        assert selection.eligible_comparable_count == 12
        assert len({row["transaction_id"] for row in selection.all_rows}) == 12
