"""Comparison orchestration — immediate metrics plus enriched data."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.utils import inputs_hash
from app.engines.immediate_comparison import ImmediateComparisonEngine
from app.engines.preference_scoring import PreferenceScoringEngine
from app.engines.recommendation import RecommendationEngine
from app.engines.requirement_engine import RequirementEngine
from app.repositories.enrichment_repository import EnrichmentRepository
from app.repositories.session_repository import SessionRepository
from app.schemas.comparison import ComparisonResponse, MetricResultSchema, RecommendationSchema
from app.services.enrichment_service import EnrichmentService
from app.services.observation_service import ObservationService

_DRIVING_COMPONENT_WEIGHTS = {
    "major_road_access": 0.30,
    "route_connectivity": 0.25,
    "peak_access_penalty": 0.25,
    "parking_convenience": 0.20,
}


def _buyer_driving(value: dict) -> dict:
    """Normalize current and legacy persisted driving fields for the buyer API.

    Older enrichment rows may still contain ``driving_time_to_destinations``
    and the previous weights. They are not allowed to leak into the new
    general rollup or recommendation score while the row is waiting for a
    fresh enrichment run.
    """
    payload = dict(value)
    raw_components = payload.get("components")
    components = []
    for component in raw_components if isinstance(raw_components, list) else []:
        if not isinstance(component, dict) or component.get("name") not in _DRIVING_COMPONENT_WEIGHTS:
            continue
        normalized = dict(component)
        normalized["weight"] = _DRIVING_COMPONENT_WEIGHTS[normalized["name"]]
        components.append(normalized)

    assessed = [
        component
        for component in components
        if component.get("status") in {"calculated", "estimated"} and isinstance(component.get("score"), (int, float))
    ]
    assessed_weight = sum(float(component["weight"]) for component in assessed)
    coverage = round(assessed_weight, 2)
    score = (
        sum(float(component["score"]) * float(component["weight"]) for component in assessed) / assessed_weight
        if assessed_weight
        else None
    )
    display_score = round(score, 1) if score is not None else None
    complete = len(assessed) == len(_DRIVING_COMPONENT_WEIGHTS)
    eligible = bool(assessed) and coverage >= 0.60
    excluded = [name for name in _DRIVING_COMPONENT_WEIGHTS if name not in {c["name"] for c in assessed}]
    payload.update(
        {
            "components": components,
            "display_score": display_score,
            "unrounded_score": score,
            "overall_score": display_score if eligible else None,
            "is_complete": complete,
            "counts_toward_recommendation": eligible,
            "coverage_ratio": coverage,
            "assessed_components": [c["name"] for c in assessed],
            "excluded_components": excluded,
            "warnings": (
                [
                    f"Partial result — {', '.join(excluded)} could not be assessed; "
                    "remaining component weights were renormalised for display only."
                ]
                if assessed and not complete
                else []
            ),
        }
    )
    return payload


def _json_safe(obj: object) -> object:
    """Convert domain objects, enum values, UUIDs, and mapping keys to JSON-safe data."""

    def normalize(value: object) -> object:
        if is_dataclass(value):
            return normalize(asdict(value))
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, dict):
            return {str(key): normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [normalize(item) for item in value]
        return value

    return json.loads(json.dumps(normalize(obj), default=str))


def _buyer_fair_price(value: dict) -> dict:
    """Return capped contextual evidence without exposing the audit collection."""
    payload = dict(value)
    evidence = payload.get("comparable_evidence")
    evidence_count = evidence.get("eligible_comparable_count") if isinstance(evidence, dict) else None
    eligible_count = payload.get("eligible_transaction_count", evidence_count)
    displayed = payload.get("displayed_comparables")
    if not isinstance(displayed, list):
        displayed = payload.get("comparables")
    if not isinstance(displayed, list):
        displayed = []
    payload["eligible_transaction_count"] = int(eligible_count) if isinstance(eligible_count, (int, float)) else 0
    payload["displayed_comparables"] = displayed[:10]
    # Keep the old `comparables` name as a capped compatibility alias while
    # ensuring it can never contain the complete eligible collection.
    payload["comparables"] = payload["displayed_comparables"]
    payload.pop("all_comparables", None)
    return payload


class ComparisonService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SessionRepository(db)
        self.enrichment_repo = EnrichmentRepository(db)
        self.enrichment = EnrichmentService(db)

    def get_comparison(self, session_id: UUID) -> ComparisonResponse:
        session = self.repo.get_session(session_id)
        if not session:
            raise ValueError("Session not found")

        listings = self.repo.get_listings(session_id)
        profile = self.repo.get_buyer_profile(session_id)
        listing_ids = [l.listing_id for l in listings]

        immediate = ImmediateComparisonEngine.compute(listings, profile)
        journeys = self.enrichment.journey_estimates_domain(listing_ids)
        requirement_results = []
        if profile and profile.hard_requirements:
            requirement_results = RequirementEngine.evaluate_all(listings, profile, immediate, journeys)

        enriched_by_listing = {
            lid: {
                field.field_name: _buyer_driving(field.value_json)
                if field.field_name == "driving_access" and isinstance(field.value_json, dict)
                else field.value_json
                for field in self.enrichment_repo.get_enriched_fields(lid)
                if field.value_json is not None
            }
            for lid in listing_ids
        }
        preference_scores = []
        if profile:
            preference_scores = PreferenceScoringEngine.score(
                listings, listing_ids, profile, immediate, journeys, enriched_by_listing
            )

        rec, trace = RecommendationEngine.recommend(
            session_id,
            listings,
            profile,
            requirement_results,
            preference_scores,
            inputs_hash({"listings": listing_ids, "profile": profile}),
        )

        trace_payload = _json_safe(
            {
                "requirement_results": requirement_results,
                "preference_scores": preference_scores,
                "recommendation": rec,
                "listing_groups": trace.listing_groups,
            }
        )
        self.repo.save_recommendation_trace(session_id, trace_payload, trace.inputs_hash)

        # Collect enriched fields per listing
        fair_price_by_listing: dict[str, dict] = {}
        transport_by_listing: dict[str, dict] = {}
        driving_by_listing: dict[str, dict] = {}
        schools_by_listing: dict[str, dict] = {}
        fair_price_status = "NOT_STARTED"

        for lid in listing_ids:
            fields = self.enrichment_repo.get_enriched_fields(lid)
            for f in fields:
                if f.field_name == "fair_price" and f.value_json:
                    fair_price_by_listing[str(lid)] = _buyer_fair_price(f.value_json)
                    fair_price_status = f.value_json.get("status", "AVAILABLE")
                elif f.field_name == "public_transport" and f.value_json:
                    transport_by_listing[str(lid)] = f.value_json
                elif f.field_name == "driving_access" and f.value_json:
                    driving_by_listing[str(lid)] = _buyer_driving(f.value_json)
                elif f.field_name == "schools" and f.value_json:
                    schools_by_listing[str(lid)] = f.value_json

        if not fair_price_by_listing and listings:
            fair_price_status = "AWAITING_ENRICHMENT"

        journey_results = [
            {
                "journey_estimate_id": str(j.journey_estimate_id),
                "listing_id": str(j.listing_id),
                "important_location_id": str(j.important_location_id),
                "mode": j.mode.value,
                "duration_seconds": j.duration_seconds,
                "duration_minutes": round(j.duration_seconds / 60) if j.duration_seconds else None,
                "difference_from_fastest_seconds": j.difference_from_fastest_seconds,
                "is_fastest": j.is_fastest,
                "status": j.status.value,
                "provider": j.provider,
                "requested_day_type": j.requested_day_type.value,
                "requested_time_local": j.requested_time_local.isoformat(),
            }
            for j in journeys
        ]

        # Personal driving journeys are exposed separately from the
        # destination-independent Driving Connectivity rollup. Filter against
        # the current profile so removed/incomplete destinations do not leave
        # stale journey cards visible after a profile update.
        locations_by_id = {
            location.important_location_id: location
            for location in (profile.important_locations if profile else [])
            if location.is_complete
            and location.latitude is not None
            and location.longitude is not None
            and location.transport_mode is not None
        }
        regular_destination_journeys = []
        for journey in journeys:
            location = locations_by_id.get(journey.important_location_id)
            if journey.mode.value != "DRIVING" or location is None:
                continue
            regular_destination_journeys.append(
                {
                    "journey_estimate_id": str(journey.journey_estimate_id),
                    "listing_id": str(journey.listing_id),
                    "important_location_id": str(journey.important_location_id),
                    "destination_label": location.label,
                    "destination_address": location.formatted_address,
                    "selected_day_type": journey.requested_day_type.value,
                    "selected_time_local": journey.requested_time_local.isoformat(),
                    "duration_minutes": round(journey.duration_seconds / 60)
                    if journey.duration_seconds is not None
                    else None,
                    "difference_from_fastest_seconds": journey.difference_from_fastest_seconds,
                    "is_fastest": journey.is_fastest,
                    "status": journey.status.value,
                    "provider": journey.provider,
                    "provider_status": journey.provider_status,
                    "source": "traffic-aware route matrix",
                    "confidence": "estimated" if journey.duration_seconds is not None else "unavailable",
                }
            )

        enrichment_summary = self.enrichment.get_status(session_id)
        observations = ObservationService(self.db).list_for_session(session_id)

        return ComparisonResponse(
            session_id=session_id,
            listing_count=len(listings),
            can_compare=len(listings) >= 2,
            immediate_metrics=[MetricResultSchema.from_domain(m) for m in immediate],
            requirement_results=_json_safe(requirement_results),
            preference_scores=_json_safe(preference_scores),
            recommendation=RecommendationSchema.from_domain(rec),
            fair_price_status=fair_price_status,
            fair_price_by_listing=fair_price_by_listing,
            transport_by_listing=transport_by_listing,
            driving_by_listing=driving_by_listing,
            schools_by_listing=schools_by_listing,
            observations=observations,
            journey_results=journey_results,
            regular_destination_journeys=regular_destination_journeys,
            enrichment_summary=enrichment_summary,
            demo_mode=session.demo_mode,
        )
