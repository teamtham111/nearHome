"""Enrichment orchestration — runs independent jobs per listing."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.adapters.factory import (
    get_geocoding_adapter,
    get_routes_adapter,
    get_routing_provider,
    get_transactions_adapter,
)
from app.adapters.parking.hdb_availability import CarparkAvailabilityProvider
from app.adapters.parking.hdb_carpark import HdbCarparkStore
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.enums import DataStatus, EnrichmentStatus, EnrichmentType, JourneyMode, MainTransportMode
from app.domain.models import BuyerProfile, ConfirmedListing, JourneyEstimate
from app.domain.transport_models import ModelRollup
from app.engines.driving.engine import compute_driving_model
from app.engines.fair_price import FairPriceEngine
from app.engines.fair_price_comparables import (
    derive_lease_commencement_from_transactions,
    derive_town_match_evidence,
    infer_flat_model_from_transactions,
)
from app.engines.public_transport.engine import compute_public_transport_model
from app.engines.schools import SchoolsEngine
from app.models.orm import ConfirmedListingORM
from app.repositories.carpark_repository import CarparkRepository
from app.repositories.enrichment_repository import EnrichmentRepository
from app.repositories.mappers import confirmed_listing_from_orm
from app.repositories.session_repository import SessionRepository
from app.services.journey.timestamp_resolver import resolve_departure_timestamp
from app.services.lease_estimation import LeaseEvidenceCache, estimate_remaining_lease
from app.utils.hdb_address import canonical_hdb_address_key

logger = get_logger(__name__)


def _rollup_to_field(rollup: ModelRollup) -> dict:
    """Serialise a Public Transport / Driving `ModelRollup` for storage in
    `enriched_fields`. `overall_score` is only populated when
    `counts_toward_recommendation` is True — `PreferenceScoringEngine`
    reads this key and correctly drops `None` per listing, so an
    incomplete assessment can never silently influence the recommendation.
    """
    return {
        "overall_score": rollup.overall_score,
        "display_score": rollup.display_score,
        "unrounded_score": rollup.unrounded_score,
        "is_complete": rollup.is_complete,
        "counts_toward_recommendation": rollup.counts_toward_recommendation,
        "coverage_ratio": rollup.coverage_ratio,
        "assessed_components": rollup.assessed_component_names,
        "excluded_components": rollup.excluded_component_names,
        "warnings": rollup.warnings,
        "components": [c.to_dict() for c in rollup.components],
    }


def _rollup_provenance(rollup: ModelRollup) -> str:
    provenances = {c.provenance.value for c in rollup.components if c.is_assessed}
    if not provenances:
        return "unavailable"
    if "ROUTED_LIVE" in provenances:
        return "ROUTED_LIVE"
    return sorted(provenances)[0]


class EnrichmentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = EnrichmentRepository(db)
        self.carpark_repo = CarparkRepository(db)
        self.session_repo = SessionRepository(db)
        self.lease_cache = LeaseEvidenceCache()

    def plan_enrichments(self, session_id: UUID, profile: BuyerProfile | None) -> list[str]:
        """Return enrichment types needed for this session."""
        types = [
            EnrichmentType.GEOCODING.value,
            EnrichmentType.PROPERTY_DATA.value,
            EnrichmentType.TRANSACTION_DATA.value,
            EnrichmentType.LEASE.value,
            EnrichmentType.FAIR_PRICE.value,
        ]
        if profile:
            if profile.main_transport_mode in (
                MainTransportMode.MAINLY_PUBLIC_TRANSPORT,
                MainTransportMode.BOTH,
            ):
                types.append(EnrichmentType.PUBLIC_TRANSPORT.value)
            if profile.main_transport_mode in (MainTransportMode.MAINLY_DRIVING, MainTransportMode.BOTH):
                types.append(EnrichmentType.DRIVING_ACCESS.value)
            if profile.schools_matter:
                types.append(EnrichmentType.SCHOOLS.value)
            for loc in profile.important_locations:
                if not loc.is_complete:
                    continue
                if loc.transport_mode in (JourneyMode.PUBLIC_TRANSPORT, JourneyMode.BOTH):
                    types.append(EnrichmentType.IMPORTANT_LOCATION_PT.value)
                if loc.transport_mode in (JourneyMode.DRIVING, JourneyMode.BOTH):
                    types.append(EnrichmentType.IMPORTANT_LOCATION_DRIVING.value)
        return list(dict.fromkeys(types))

    def run_session_enrichment(self, session_id: UUID, simulate_delay: bool = True) -> dict:
        session = self.session_repo.get_session(session_id)
        if not session:
            raise ValueError("Session not found")
        profile = self.session_repo.get_buyer_profile(session_id)
        listings = session.listings
        results = {"listings": [], "errors": []}

        availability_provider = CarparkAvailabilityProvider()
        if profile and profile.main_transport_mode in (MainTransportMode.MAINLY_DRIVING, MainTransportMode.BOTH):
            # Static records are refreshed by the pipeline and mirrored here so
            # production comparisons have an auditable official-data copy.
            try:
                self.carpark_repo.replace_static_records(HdbCarparkStore.load())
            except Exception as exc:
                results["errors"].append(f"HDB carpark persistence unavailable: {exc}")

        for listing_orm in listings:
            listing = confirmed_listing_from_orm(listing_orm)
            listing_result = self._enrich_listing(listing, profile, simulate_delay, availability_provider)
            results["listings"].append(listing_result)

        if profile and profile.important_locations:
            self._enrich_journeys(listings, profile, simulate_delay)

        return results

    def _enrich_listing(
        self,
        listing: ConfirmedListing,
        profile: BuyerProfile | None,
        simulate_delay: bool,
        availability_provider: CarparkAvailabilityProvider | None = None,
    ) -> dict:
        lid = listing.listing_id
        town: str | None = None
        town_source: str | None = None
        town_derivation: dict[str, object] | None = None
        lat: float | None = None
        lng: float | None = None
        transaction_records = []
        try:
            transaction_records = get_transactions_adapter().all_records()
        except Exception:
            transaction_records = []

        # Geocoding
        self.repo.upsert_run(lid, EnrichmentType.GEOCODING.value, EnrichmentStatus.RUNNING.value)
        if simulate_delay:
            time.sleep(0.05)
        try:
            geo = get_geocoding_adapter().geocode(listing.address)
            if geo.town:
                town = geo.town
                town_source = "reverse_geocode"
                address_key = canonical_hdb_address_key(listing.address)
                town_derivation = {
                    "raw_listing_address": listing.address,
                    "canonical_address_key": (
                        {"block": address_key[0], "street": address_key[1]} if address_key else None
                    ),
                    "matching_transaction_count": None,
                    "matched_towns": [geo.town],
                    "town": geo.town,
                    "source": "reverse_geocode",
                    "reason": "authoritative_geocoder_town",
                }
            else:
                town_derivation = derive_town_match_evidence(transaction_records, listing.address)
                town = town_derivation["town"]
                town_source = town_derivation["source"]
            lat, lng = geo.latitude, geo.longitude
            self.repo.save_enriched_field(
                lid,
                "geocode",
                {
                    "latitude": geo.latitude,
                    "longitude": geo.longitude,
                    "formatted_address": geo.formatted_address,
                    "postal_code": geo.postal_code,
                    "town": town,
                    "town_source": town_source,
                    "town_derivation": town_derivation,
                    "provider": geo.provider,
                    "provenance": geo.provenance,
                },
                DataStatus.AVAILABLE.value,
                geo.provider,
                provenance=geo.provenance,
            )
            self.repo.upsert_run(lid, EnrichmentType.GEOCODING.value, EnrichmentStatus.SUCCEEDED.value)
        except Exception as e:
            self.repo.upsert_run(lid, EnrichmentType.GEOCODING.value, EnrichmentStatus.FAILED.value, str(e))

        # Resolve one canonical, source-aware lease estimate. Persist the
        # result as well as passing the enriched copy to valuation so every
        # downstream API consumer sees the same canonical lease fields.
        self.repo.upsert_run(lid, EnrichmentType.LEASE.value, EnrichmentStatus.RUNNING.value)
        valuation_listing = listing
        if listing.lease_commencement_year is None:
            commencement_year, _ = derive_lease_commencement_from_transactions(
                transaction_records,
                listing.address,
            )
            if commencement_year is not None:
                valuation_listing = replace(valuation_listing, lease_commencement_year=commencement_year)

        lease_estimate = estimate_remaining_lease(
            valuation_listing,
            transaction_records,
            cache=self.lease_cache,
        )
        if lease_estimate.remaining_lease_months is not None:
            months = lease_estimate.remaining_lease_months
            valuation_listing = replace(
                valuation_listing,
                remaining_lease_months=months,
                remaining_lease_years=round(months / 12, 2),
                remaining_lease_source=lease_estimate.source,
                remaining_lease_confidence=lease_estimate.confidence,
                remaining_lease_as_of_date=date.fromisoformat(lease_estimate.as_of_date),
                remaining_lease_status=DataStatus.AVAILABLE,
                lease_commencement_year=lease_estimate.lease_commencement_year
                or valuation_listing.lease_commencement_year,
            )
        else:
            valuation_listing = replace(
                valuation_listing,
                remaining_lease_months=None,
                remaining_lease_years=None,
                remaining_lease_source="unavailable",
                remaining_lease_confidence="unavailable",
                remaining_lease_as_of_date=date.fromisoformat(lease_estimate.as_of_date),
                remaining_lease_status=DataStatus.NOT_PROVIDED_BY_USER,
            )
        self.session_repo.update_lease_estimate(
            lid,
            lease_commencement_year=valuation_listing.lease_commencement_year,
            remaining_lease_months=valuation_listing.remaining_lease_months,
            remaining_lease_years=valuation_listing.remaining_lease_years,
            remaining_lease_source=valuation_listing.remaining_lease_source or lease_estimate.source,
            remaining_lease_confidence=valuation_listing.remaining_lease_confidence or lease_estimate.confidence,
            remaining_lease_as_of_date=valuation_listing.remaining_lease_as_of_date
            or date.fromisoformat(lease_estimate.as_of_date),
            remaining_lease_status=valuation_listing.remaining_lease_status,
        )
        self.repo.save_enriched_field(
            lid,
            "remaining_lease_estimate",
            lease_estimate.to_dict(),
            DataStatus.AVAILABLE.value
            if lease_estimate.remaining_lease_months is not None
            else DataStatus.UNAVAILABLE.value,
            "HDB_LEASE_ESTIMATOR",
            confidence=lease_estimate.confidence.upper(),
            provenance=lease_estimate.source.upper(),
        )
        if lease_estimate.lease_commencement_year is not None:
            self.repo.save_enriched_field(
                lid,
                "lease_commencement_year",
                lease_estimate.lease_commencement_year,
                DataStatus.AVAILABLE.value,
                "HDB_LEASE_ESTIMATOR",
                confidence=lease_estimate.confidence.upper(),
                provenance=lease_estimate.source.upper(),
            )
        if lease_estimate.remaining_lease_months is not None:
            self.repo.save_enriched_field(
                lid,
                "remaining_lease_months",
                lease_estimate.remaining_lease_months,
                DataStatus.AVAILABLE.value,
                "HDB_LEASE_ESTIMATOR",
                confidence=lease_estimate.confidence.upper(),
                provenance=lease_estimate.source.upper(),
            )
        if not listing.flat_model and transaction_records:
            inferred_model, model_source = infer_flat_model_from_transactions(transaction_records, listing)
            if inferred_model:
                valuation_listing = replace(
                    valuation_listing,
                    flat_model=inferred_model,
                    flat_model_source=model_source,
                )
                self.repo.save_enriched_field(
                    lid,
                    "hdb_flat_attributes",
                    {"flat_model": inferred_model, "flat_model_source": model_source},
                    DataStatus.AVAILABLE.value,
                    "HDB_TRANSACTIONS",
                    confidence="MEDIUM",
                    provenance="INFERRED",
                )
        lease_provenance = lease_estimate.source.upper()
        self.repo.upsert_run(lid, EnrichmentType.LEASE.value, EnrichmentStatus.SUCCEEDED.value)

        # Fair price
        self.repo.upsert_run(lid, EnrichmentType.FAIR_PRICE.value, EnrichmentStatus.RUNNING.value)
        fp = FairPriceEngine.estimate(valuation_listing, town, town_source=town_source, records=transaction_records)
        if settings.app_env == "development":
            filter_status = fp.filter_status or {}
            applied = [
                name
                for name, state in filter_status.items()
                if isinstance(state, dict) and state.get("status") == "applied"
            ]
            omitted = [
                name
                for name, state in filter_status.items()
                if isinstance(state, dict) and state.get("status") == "omitted_missing"
            ]
            logger.info(
                "FAIR_PRICE_FILTER_DIAGNOSTIC",
                confirmed_listing={
                    "town": town,
                    "town_source": town_source,
                    "flat_type": valuation_listing.flat_type,
                    "listing_flat_subtype": valuation_listing.listing_flat_subtype,
                    "flat_model": valuation_listing.flat_model,
                    "storey_range": valuation_listing.storey_range,
                },
                filters_applied=applied,
                filters_omitted=omitted,
                relaxation_steps=filter_status.get("relaxation_steps", []),
                comparable_count_by_stage=fp.comparable_count_by_stage or {},
            )
        self.repo.save_enriched_field(
            lid,
            "fair_price",
            {
                "central_estimate": fp.central_estimate,
                "range_low": fp.range_low,
                "range_high": fp.range_high,
                "asking_difference_dollars": fp.asking_difference_dollars,
                "asking_difference_pct": fp.asking_difference_pct,
                "value_gap_percentage": fp.value_gap_percentage,
                "confidence": fp.confidence.value,
                "confidence_reasons": fp.confidence_reasons,
                "comparables": fp.comparables,
                "displayed_comparables": fp.comparables,
                "eligible_transaction_count": (fp.evidence or {}).get("eligible_comparable_count", 0),
                "all_comparables": fp.all_comparables or [],
                "method": fp.method,
                "status": fp.status.value,
                "model_version": fp.model_version,
                "final_estimate": fp.central_estimate,
                "warnings": fp.warnings or [],
                "comparable_evidence": fp.evidence or {},
                "comparable_model_version": fp.comparable_model_version,
                "filter_status": fp.filter_status or {},
                "filter_messages": fp.filter_messages or [],
                "warning_details": fp.warning_details or [],
                "comparable_count_by_stage": fp.comparable_count_by_stage or {},
                "town": town,
                "town_source": town_source,
                "flat_model_used": valuation_listing.flat_model,
                "flat_model_source": valuation_listing.flat_model_source,
                "remaining_lease_years_used": valuation_listing.remaining_lease_years,
                "remaining_lease_months_used": valuation_listing.remaining_lease_months,
                "remaining_lease_status": valuation_listing.remaining_lease_status.value,
                "remaining_lease_provenance": lease_provenance,
                "remaining_lease_source": lease_estimate.source,
                "remaining_lease_confidence": lease_estimate.confidence,
                "remaining_lease_as_of_date": lease_estimate.as_of_date,
                "remaining_lease_estimate": lease_estimate.to_dict(),
            },
            fp.status.value,
            "FAIR_PRICE_ENGINE",
            confidence=fp.confidence.value,
        )
        self.repo.upsert_run(
            lid,
            EnrichmentType.FAIR_PRICE.value,
            EnrichmentStatus.SUCCEEDED.value
            if fp.status != DataStatus.INSUFFICIENT_EVIDENCE
            else EnrichmentStatus.UNAVAILABLE.value,
        )

        # Public transport
        if profile and profile.main_transport_mode in (
            MainTransportMode.MAINLY_PUBLIC_TRANSPORT,
            MainTransportMode.BOTH,
        ):
            self.repo.upsert_run(lid, EnrichmentType.PUBLIC_TRANSPORT.value, EnrichmentStatus.RUNNING.value)
            if lat is None or lng is None:
                self.repo.upsert_run(
                    lid,
                    EnrichmentType.PUBLIC_TRANSPORT.value,
                    EnrichmentStatus.UNAVAILABLE.value,
                    "Coordinates unavailable",
                )
            else:
                pt_rollup = compute_public_transport_model(lat, lng, get_routing_provider())
                self.repo.save_enriched_field(
                    lid,
                    "public_transport",
                    _rollup_to_field(pt_rollup),
                    DataStatus.AVAILABLE.value if pt_rollup.display_score is not None else DataStatus.UNAVAILABLE.value,
                    "PUBLIC_TRANSPORT_ENGINE",
                    provenance=_rollup_provenance(pt_rollup),
                )
                self.repo.upsert_run(
                    lid,
                    EnrichmentType.PUBLIC_TRANSPORT.value,
                    EnrichmentStatus.SUCCEEDED.value
                    if pt_rollup.display_score is not None
                    else EnrichmentStatus.UNAVAILABLE.value,
                )

        # Driving
        if profile and profile.main_transport_mode in (MainTransportMode.MAINLY_DRIVING, MainTransportMode.BOTH):
            self.repo.upsert_run(lid, EnrichmentType.DRIVING_ACCESS.value, EnrichmentStatus.RUNNING.value)
            if lat is None or lng is None:
                self.repo.upsert_run(
                    lid,
                    EnrichmentType.DRIVING_ACCESS.value,
                    EnrichmentStatus.UNAVAILABLE.value,
                    "Coordinates unavailable",
                )
            else:
                dr_rollup = compute_driving_model(
                    lat,
                    lng,
                    get_routing_provider(),
                    availability_provider=availability_provider,
                    history_lookup=self.carpark_repo.historical_summary,
                    listing_address=listing.address,
                )
                parking_component = next(
                    (component for component in dr_rollup.components if component.name == "parking_convenience"), None
                )
                if parking_component is not None:
                    try:
                        self.carpark_repo.save_matches_and_metric(lid, parking_component)
                    except Exception as exc:
                        self.repo.save_enriched_field(
                            lid,
                            "parking_persistence_warning",
                            {"message": str(exc)},
                            DataStatus.TEMPORARILY_UNAVAILABLE.value,
                            "HDB_CARPARK_DATABASE",
                        )
                self.repo.save_enriched_field(
                    lid,
                    "driving_access",
                    _rollup_to_field(dr_rollup),
                    DataStatus.AVAILABLE.value if dr_rollup.display_score is not None else DataStatus.UNAVAILABLE.value,
                    "DRIVING_ENGINE",
                    provenance=_rollup_provenance(dr_rollup),
                )
                self.repo.upsert_run(
                    lid,
                    EnrichmentType.DRIVING_ACCESS.value,
                    EnrichmentStatus.SUCCEEDED.value
                    if dr_rollup.display_score is not None
                    else EnrichmentStatus.UNAVAILABLE.value,
                )

        # Schools
        if profile and profile.schools_matter:
            self.repo.upsert_run(lid, EnrichmentType.SCHOOLS.value, EnrichmentStatus.RUNNING.value)
            schools = SchoolsEngine.compute(lid, lat, lng, named_schools=profile.school_names)
            self.repo.save_enriched_field(
                lid,
                "schools",
                SchoolsEngine.to_dict(schools),
                schools.status.value,
                "SCHOOLS_ENGINE",
                provenance=schools.provenance.value,
            )
            self.repo.upsert_run(
                lid,
                EnrichmentType.SCHOOLS.value,
                EnrichmentStatus.SUCCEEDED.value if schools.score is not None else EnrichmentStatus.UNAVAILABLE.value,
            )

        self.repo.upsert_run(lid, EnrichmentType.TRANSACTION_DATA.value, EnrichmentStatus.SUCCEEDED.value)
        self.repo.upsert_run(lid, EnrichmentType.PROPERTY_DATA.value, EnrichmentStatus.SUCCEEDED.value)

        return {"listing_id": str(lid), "town": town, "lat": lat, "lng": lng}

    def _enrich_journeys(
        self, listings: list[ConfirmedListingORM], profile: BuyerProfile, simulate_delay: bool
    ) -> None:
        routes = get_routes_adapter()
        coords: list[tuple[float, float]] = []
        listing_ids: list[UUID] = []

        for listing_orm in listings:
            listing = confirmed_listing_from_orm(listing_orm)
            geo_field = self.repo.get_field(listing.listing_id, "geocode")
            if geo_field and geo_field.value_json:
                coords.append((geo_field.value_json["latitude"], geo_field.value_json["longitude"]))
                listing_ids.append(listing.listing_id)
            else:
                coords.append((1.3521, 103.8498))
                listing_ids.append(listing.listing_id)

        for loc in profile.important_locations:
            if not loc.is_complete or loc.latitude is None or loc.longitude is None:
                continue
            if loc.usual_day_type is None or loc.departure_time_local is None:
                continue

            departure = resolve_departure_timestamp(loc.usual_day_type, loc.departure_time_local)
            dest = (loc.latitude, loc.longitude)
            modes: list[str] = []
            if loc.transport_mode in (JourneyMode.PUBLIC_TRANSPORT, JourneyMode.BOTH):
                modes.append("PUBLIC_TRANSPORT")
            if loc.transport_mode in (JourneyMode.DRIVING, JourneyMode.BOTH):
                modes.append("DRIVING")

            for mode in modes:
                etype = (
                    EnrichmentType.IMPORTANT_LOCATION_PT.value
                    if mode == "PUBLIC_TRANSPORT"
                    else EnrichmentType.IMPORTANT_LOCATION_DRIVING.value
                )
                for lid in listing_ids:
                    self.repo.upsert_run(lid, etype, EnrichmentStatus.RUNNING.value)

                if simulate_delay:
                    time.sleep(0.05)

                try:
                    matrix = routes.route_matrix(coords, dest, mode, departure)
                except Exception as exc:
                    for lid in listing_ids:
                        # Keep a local, destination-scoped failure record so
                        # the UI can explain the journey failure without
                        # invalidating general transport/driving scores.
                        self.repo.save_journey_estimate(
                            listing_id=lid,
                            important_location_id=loc.important_location_id,
                            mode=mode,
                            requested_day_type=loc.usual_day_type.value,
                            requested_time_local=loc.departure_time_local,
                            timezone=loc.timezone,
                            resolved_departure_at=departure,
                            duration_seconds=None,
                            difference_from_fastest_seconds=None,
                            is_fastest=None,
                            status=DataStatus.TEMPORARILY_UNAVAILABLE.value,
                            provider=getattr(routes, "provider_name", "ROUTE_PROVIDER"),
                            provider_status=str(exc)[:100],
                        )
                        self.repo.upsert_run(
                            lid,
                            etype,
                            EnrichmentStatus.FAILED.value,
                            "Route provider unavailable",
                        )
                    continue
                durations: list[int | None] = []
                for elem in matrix.elements:
                    durations.append(elem.duration_seconds)

                available = [d for d in durations if d is not None]
                fastest = min(available) if available else None

                for idx, lid in enumerate(listing_ids):
                    elem = matrix.elements[idx] if idx < len(matrix.elements) else None
                    dur = elem.duration_seconds if elem else None
                    diff = (dur - fastest) if dur is not None and fastest is not None else None
                    is_fastest = dur == fastest if dur is not None else None
                    status = DataStatus.AVAILABLE.value if dur is not None else DataStatus.TEMPORARILY_UNAVAILABLE.value

                    self.repo.save_journey_estimate(
                        listing_id=lid,
                        important_location_id=loc.important_location_id,
                        mode=mode,
                        requested_day_type=loc.usual_day_type.value,
                        requested_time_local=loc.departure_time_local,
                        timezone=loc.timezone,
                        resolved_departure_at=matrix.resolved_departure_at,
                        duration_seconds=dur,
                        difference_from_fastest_seconds=diff,
                        is_fastest=is_fastest,
                        status=status,
                        provider=matrix.provider,
                        provider_status=elem.provider_status if elem else None,
                    )
                    self.repo.upsert_run(lid, etype, EnrichmentStatus.SUCCEEDED.value)

    def get_status(self, session_id: UUID) -> list[dict]:
        session = self.session_repo.get_session(session_id)
        if not session:
            return []
        listing_ids = [l.id for l in session.listings]
        runs = self.repo.get_runs_for_session_listings(listing_ids)
        return [
            {
                "listing_id": str(r.listing_id),
                "enrichment_type": r.enrichment_type,
                "status": r.status,
                "error_message": r.error_message,
            }
            for r in runs
        ]

    def journey_estimates_domain(self, listing_ids: list[UUID]) -> list[JourneyEstimate]:
        from app.domain.enums import DayType

        rows = self.repo.get_journey_estimates(listing_ids)
        return [
            JourneyEstimate(
                journey_estimate_id=r.id,
                listing_id=r.listing_id,
                important_location_id=r.important_location_id,
                mode=JourneyMode(r.mode),
                requested_day_type=DayType(r.requested_day_type),
                requested_time_local=r.requested_time_local,
                timezone=r.timezone,
                resolved_departure_at=r.resolved_departure_at.isoformat() if r.resolved_departure_at else None,
                duration_seconds=r.duration_seconds,
                difference_from_fastest_seconds=r.difference_from_fastest_seconds,
                is_fastest=r.is_fastest,
                status=DataStatus(r.status),
                provider=r.provider,
                provider_status=r.provider_status,
                retrieved_at=r.retrieved_at,
            )
            for r in rows
        ]
