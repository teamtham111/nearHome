"""Deterministic recommendation engine — LLM must not select the winner."""

from __future__ import annotations

from datetime import UTC
from uuid import UUID, uuid4

from app.core.config import settings
from app.domain.enums import (
    ListingGroup,
    RecommendationConfidence,
    RequirementStatus,
)
from app.domain.models import (
    BuyerProfile,
    ConfirmedListing,
    PreferenceScore,
    RecommendationResult,
    RecommendationTrace,
    RequirementResult,
)
from app.engines.preference_scoring import PreferenceScoringEngine
from app.engines.requirement_engine import RequirementEngine


class RecommendationEngine:
    RULE_VERSION = settings.recommendation_rule_version

    @classmethod
    def recommend(
        cls,
        session_id: UUID,
        listings: list[ConfirmedListing],
        buyer_profile: BuyerProfile | None,
        requirement_results: list[RequirementResult],
        preference_scores: list[PreferenceScore],
        inputs_hash: str,
    ) -> tuple[RecommendationResult, RecommendationTrace]:
        listing_ids = [l.listing_id for l in listings]
        groups = RequirementEngine.classify_listings(listing_ids, requirement_results)
        eligible_ids = RequirementEngine.eligible_listing_ids(listing_ids, groups)

        has_passing = any(groups[lid] == ListingGroup.PASSES_ALL for lid in listing_ids)
        has_only_fails = all(
            groups[lid] in (ListingGroup.FAILS_ONE, ListingGroup.FAILS_MULTIPLE)
            for lid in listing_ids
        )

        if not buyer_profile or not buyer_profile.priorities:
            rec = cls._factual_only(listings, groups, has_passing, has_only_fails)
            trace = cls._build_trace(session_id, requirement_results, preference_scores, rec, groups, inputs_hash)
            return rec, trace

        eligible_scores = [
            score
            for score in preference_scores
            if score.listing_id in eligible_ids and score.overall_fit_score is not None
        ]
        rec = cls._rank(
            listings, groups, eligible_ids, eligible_scores, has_passing, has_only_fails, requirement_results
        )
        trace = cls._build_trace(session_id, requirement_results, preference_scores, rec, groups, inputs_hash)
        return rec, trace

    @classmethod
    def _factual_only(
        cls,
        listings: list[ConfirmedListing],
        groups: dict[UUID, ListingGroup],
        has_passing: bool,
        has_only_fails: bool,
    ) -> RecommendationResult:
        if has_only_fails:
            return RecommendationResult(
                recommended_listing_id=None,
                is_tie=False,
                is_provisional=True,
                eligible_group=ListingGroup.FAILS_MULTIPLE,
                reason="No listing meets all supported requirements",
                one_sentence_summary="None of your shortlisted flats satisfy every supported requirement.",
                advantages={str(l.listing_id): [] for l in listings},
                compromises={str(l.listing_id): ["Fails one or more requirements"] for l in listings},
                missing_information=["Select priorities for a personalised recommendation"],
                confidence=RecommendationConfidence.PROVISIONAL,
                confidence_reasons=["No priorities configured"],
                why_not_selected={str(l.listing_id): "Fails requirements" for l in listings},
                decision_hinge=None,
                reason_codes=["NO_PASSING_LISTINGS"],
                rule_version=cls.RULE_VERSION,
                scoring_version=PreferenceScoringEngine.SCORING_VERSION,
            )
        return RecommendationResult(
            recommended_listing_id=None,
            is_tie=False,
            is_provisional=True,
            eligible_group=ListingGroup.PASSES_ALL if has_passing else ListingGroup.CANNOT_DETERMINE,
            reason="Factual comparison available; priorities needed for personalised recommendation",
            one_sentence_summary="Compare the factual metrics below, then add up to three priorities.",
            advantages={str(l.listing_id): [] for l in listings},
            compromises={str(l.listing_id): [] for l in listings},
            missing_information=["Up to three buyer priorities"],
            confidence=RecommendationConfidence.PROVISIONAL,
            confidence_reasons=["Priorities not yet selected"],
            why_not_selected={},
            decision_hinge=None,
            reason_codes=["NO_PRIORITIES"],
            rule_version=cls.RULE_VERSION,
            scoring_version=PreferenceScoringEngine.SCORING_VERSION,
        )

    @classmethod
    def _rank(
        cls,
        listings: list[ConfirmedListing],
        groups: dict[UUID, ListingGroup],
        eligible_ids: list[UUID],
        scores: list[PreferenceScore],
        has_passing: bool,
        has_only_fails: bool,
        requirement_results: list[RequirementResult],
    ) -> RecommendationResult:
        listing_map = {l.listing_id: l for l in listings}

        if has_only_fails:
            near_misses = cls._near_miss_explanations(listings, requirement_results)
            return RecommendationResult(
                recommended_listing_id=None,
                is_tie=False,
                is_provisional=False,
                eligible_group=ListingGroup.FAILS_MULTIPLE,
                reason="No listing passes all supported requirements",
                one_sentence_summary="None of your flats meet every supported requirement. Review near-misses below.",
                advantages={str(l.listing_id): [] for l in listings},
                compromises={str(l.listing_id): near_misses.get(str(l.listing_id), []) for l in listings},
                missing_information=[],
                confidence=RecommendationConfidence.HIGH,
                confidence_reasons=["All supported requirements evaluated"],
                why_not_selected={
                    str(l.listing_id): "Fails at least one supported requirement"
                    for l in listings
                    if groups[l.listing_id] != ListingGroup.PASSES_ALL
                },
                decision_hinge=None,
                reason_codes=["ALL_FAIL_REQUIREMENTS"],
                rule_version=cls.RULE_VERSION,
                scoring_version=PreferenceScoringEngine.SCORING_VERSION,
            )

        if not scores:
            return cls._no_assessed_priority_evidence(listings, groups, has_passing)

        sorted_scores = sorted(
            scores,
            key=lambda score: score.overall_fit_score if score.overall_fit_score is not None else score.total_score,
            reverse=True,
        )
        top = sorted_scores[0]
        second = sorted_scores[1] if len(sorted_scores) > 1 else None

        is_tie = top.is_tie_candidate or (
            second is not None and abs(cls._overall_fit(top) - cls._overall_fit(second)) <= 3.0
        )

        if is_tie and second:
            return RecommendationResult(
                recommended_listing_id=None,
                is_tie=True,
                is_provisional=False,
                eligible_group=ListingGroup.PASSES_ALL if has_passing else ListingGroup.CANNOT_DETERMINE,
                reason="Practical tie between top listings",
                one_sentence_summary=(
                    f"Practical tie between {listing_map[top.listing_id].display_name} "
                    f"and {listing_map[second.listing_id].display_name} on your priorities."
                ),
                advantages=cls._build_advantages(listings, sorted_scores),
                compromises=cls._build_compromises(listings, sorted_scores),
                missing_information=cls._missing_info(sorted_scores),
                confidence=RecommendationConfidence.MEDIUM,
                confidence_reasons=["Scores within practical tie threshold"],
                why_not_selected=cls._why_not(listings, top.listing_id, sorted_scores),
                decision_hinge="Small priority-score differences; review raw metrics",
                reason_codes=["PRACTICAL_TIE"],
                rule_version=cls.RULE_VERSION,
                scoring_version=PreferenceScoringEngine.SCORING_VERSION,
            )

        winner = listing_map[top.listing_id]
        summary = cls._winner_summary(winner.display_name, top)
        return RecommendationResult(
            recommended_listing_id=top.listing_id,
            is_tie=False,
            is_provisional=top.coverage < 1.0,
            eligible_group=ListingGroup.PASSES_ALL if has_passing else ListingGroup.CANNOT_DETERMINE,
            reason="Highest overall fit score among eligible listings",
            one_sentence_summary=summary,
            advantages=cls._build_advantages(listings, sorted_scores),
            compromises=cls._build_compromises(listings, sorted_scores),
            missing_information=cls._missing_info(sorted_scores),
            confidence=RecommendationConfidence.HIGH if top.coverage >= 1.0 else RecommendationConfidence.MEDIUM,
            confidence_reasons=(
                ["All priority metrics available"]
                if top.coverage >= 1.0
                else [f"Priority coverage {int(top.coverage * 100)}%"]
            ),
            why_not_selected=cls._why_not(listings, top.listing_id, sorted_scores),
            decision_hinge=None,
            reason_codes=["PREFERENCE_LEADER"],
            rule_version=cls.RULE_VERSION,
            scoring_version=PreferenceScoringEngine.SCORING_VERSION,
        )

    @staticmethod
    def _no_assessed_priority_evidence(
        listings: list[ConfirmedListing],
        groups: dict[UUID, ListingGroup],
        has_passing: bool,
    ) -> RecommendationResult:
        return RecommendationResult(
            recommended_listing_id=None,
            is_tie=False,
            is_provisional=True,
            eligible_group=ListingGroup.PASSES_ALL if has_passing else ListingGroup.CANNOT_DETERMINE,
            reason="No assessed priority evidence is available yet",
            one_sentence_summary=(
                "Run enrichment or add the missing information before NearHome can calculate an overall fit."
            ),
            advantages={str(listing.listing_id): [] for listing in listings},
            compromises={str(listing.listing_id): [] for listing in listings},
            missing_information=["Priority evidence has not been assessed"],
            confidence=RecommendationConfidence.PROVISIONAL,
            confidence_reasons=["No selected priority has an assessed score"],
            why_not_selected={},
            decision_hinge=None,
            reason_codes=["NO_ASSESSED_PRIORITY_DATA"],
            rule_version=RecommendationEngine.RULE_VERSION,
            scoring_version=PreferenceScoringEngine.SCORING_VERSION,
        )

    @staticmethod
    def _winner_summary(display_name: str, score: PreferenceScore) -> str:
        labels = {
            "PUBLIC_TRANSPORT": "public-transport connectivity",
            "DRIVING": "driving access",
            "SCHOOLS": "school access",
            "AFFORDABILITY": "affordability",
            "SPACE": "floor area",
            "LEASE": "remaining lease",
            "IMPORTANT_LOCATION_JOURNEY": "journey convenience",
        }
        dimensions = [
            labels[key]
            for key, value in score.raw_values.items()
            if key in labels and value is not None
        ]
        if dimensions:
            return f"{display_name} ranks highest on {', '.join(dimensions[:2])} among the available evidence."
        return f"{display_name} is the best defensible fit based on your priorities."

    @staticmethod
    def _near_miss_explanations(
        listings: list[ConfirmedListing],
        requirement_results: list[RequirementResult],
    ) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for listing in listings:
            fails = [
                r
                for r in requirement_results
                if r.listing_id == listing.listing_id and r.status == RequirementStatus.FAIL
            ]
            out[str(listing.listing_id)] = [r.explanation for r in fails]
        return out

    @staticmethod
    def _build_advantages(
        listings: list[ConfirmedListing], scores: list[PreferenceScore]
    ) -> dict[str, list[str]]:
        if not scores:
            return {}
        best_sub: dict[str, float] = {}
        for ps in scores:
            for metric, val in ps.sub_scores.items():
                if metric not in best_sub or val > best_sub[metric]:
                    best_sub[metric] = val
        result: dict[str, list[str]] = {}
        for ps in scores:
            adv = [m for m, v in ps.sub_scores.items() if v >= best_sub.get(m, 0) * 0.99 and v > 0]
            result[str(ps.listing_id)] = adv[:3]
        return result

    @staticmethod
    def _build_compromises(
        listings: list[ConfirmedListing], scores: list[PreferenceScore]
    ) -> dict[str, list[str]]:
        if len(scores) < 2:
            return {str(s.listing_id): [] for s in scores}
        top_score = max(RecommendationEngine._overall_fit(s) for s in scores)
        result: dict[str, list[str]] = {}
        for ps in scores:
            gap = top_score - RecommendationEngine._overall_fit(ps)
            if gap > 3.0:
                result[str(ps.listing_id)] = ["A different balance of your selected preferences"]
            else:
                result[str(ps.listing_id)] = []
        return result

    @staticmethod
    def _missing_info(scores: list[PreferenceScore]) -> list[str]:
        missing = []
        for ps in scores:
            if ps.coverage < 1.0:
                missing.append(f"Listing {ps.listing_id}: incomplete priority data ({ps.coverage:.0%} coverage)")
        return missing

    @staticmethod
    def _why_not(
        listings: list[ConfirmedListing],
        winner_id: UUID,
        scores: list[PreferenceScore],
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for ps in scores:
            if ps.listing_id == winner_id:
                continue
            out[str(ps.listing_id)] = "Another eligible listing was recommended based on your saved preferences."
        return out

    @staticmethod
    def _overall_fit(score: PreferenceScore) -> float:
        return score.overall_fit_score if score.overall_fit_score is not None else score.total_score

    @staticmethod
    def _build_trace(
        session_id: UUID,
        requirement_results: list[RequirementResult],
        preference_scores: list[PreferenceScore],
        recommendation: RecommendationResult,
        groups: dict[UUID, ListingGroup],
        inputs_hash: str,
    ) -> RecommendationTrace:
        from datetime import datetime

        return RecommendationTrace(
            trace_id=uuid4(),
            session_id=session_id,
            requirement_results=requirement_results,
            preference_scores=preference_scores,
            recommendation=recommendation,
            listing_groups=groups,
            created_at=datetime.now(UTC),
            inputs_hash=inputs_hash,
        )
