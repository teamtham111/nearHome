"""Session and listing persistence."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.utils import content_hash, normalize_address
from app.domain.enums import DataStatus, PropertyCategory, Provenance
from app.domain.models import BuyerProfile, ConfirmedListing
from app.models.orm import (
    BuyerProfileORM,
    ComparisonSessionORM,
    ConfirmedListingORM,
    ExtractionAttemptORM,
    HardRequirementORM,
    ImportantLocationORM,
    ListingInputORM,
    RecommendationTraceORM,
)
from app.repositories.mappers import (
    buyer_profile_from_orm,
    confirmed_listing_from_orm,
    listing_input_from_orm,
)
from app.services.smart_paste.flat_attributes import normalize_flat_type, resolve_flat_property_details


class SessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_session(self, demo_mode: bool = False) -> ComparisonSessionORM:
        session = ComparisonSessionORM(demo_mode=demo_mode or settings.demo_mode)
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_session(self, session_id: UUID) -> ComparisonSessionORM | None:
        return (
            self.db.query(ComparisonSessionORM)
            .options(
                joinedload(ComparisonSessionORM.buyer_profile).joinedload(BuyerProfileORM.hard_requirements),
                joinedload(ComparisonSessionORM.buyer_profile).joinedload(BuyerProfileORM.important_locations),
                joinedload(ComparisonSessionORM.listings),
            )
            .filter(ComparisonSessionORM.id == session_id)
            .first()
        )

    def delete_session(self, session_id: UUID) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        self.db.delete(session)
        self.db.commit()
        return True

    def upsert_buyer_profile(
        self,
        session_id: UUID,
        profile: BuyerProfile,
    ) -> BuyerProfileORM:
        session = self.get_session(session_id)
        if not session:
            raise ValueError("Session not found")

        if session.buyer_profile:
            orm = session.buyer_profile
            orm.max_budget = profile.max_budget
            orm.main_transport_mode = profile.main_transport_mode.value
            orm.schools_matter = profile.schools_matter
            orm.named_schools_json = profile.school_names
            orm.named_school = profile.school_names[0] if profile.school_names else None
            orm.priorities_json = [
                {
                    "priority_type": p.priority_type.value,
                    "rank": p.rank,
                    "weight": p.weight,
                    "important_location_id": str(p.important_location_id) if p.important_location_id else None,
                }
                for p in profile.priorities
            ]
            for hr in list(orm.hard_requirements):
                self.db.delete(hr)
            for loc in list(orm.important_locations):
                self.db.delete(loc)
        else:
            orm = BuyerProfileORM(
                session_id=session_id,
                max_budget=profile.max_budget,
                main_transport_mode=profile.main_transport_mode.value,
                schools_matter=profile.schools_matter,
                named_schools_json=profile.school_names,
                named_school=profile.school_names[0] if profile.school_names else None,
                priorities_json=[
                    {
                        "priority_type": p.priority_type.value,
                        "rank": p.rank,
                        "weight": p.weight,
                        "important_location_id": str(p.important_location_id) if p.important_location_id else None,
                    }
                    for p in profile.priorities
                ],
            )
            self.db.add(orm)
            self.db.flush()

        for req in profile.hard_requirements:
            threshold_number = None
            threshold_text = None
            if isinstance(req.threshold, str):
                threshold_text = req.threshold
            else:
                threshold_number = float(req.threshold)
            self.db.add(
                HardRequirementORM(
                    buyer_profile_id=orm.id,
                    metric=req.metric.value,
                    operator=req.operator.value,
                    threshold_number=threshold_number,
                    threshold_text=threshold_text,
                    unit=req.unit,
                    label=req.label,
                    important_location_id=req.important_location_id,
                )
            )

        for loc in profile.important_locations:
            self.db.add(
                ImportantLocationORM(
                    id=loc.important_location_id,
                    buyer_profile_id=orm.id,
                    label=loc.label,
                    place_id=loc.place_id,
                    formatted_address=loc.formatted_address,
                    latitude=loc.latitude,
                    longitude=loc.longitude,
                    usual_day_type=loc.usual_day_type.value if loc.usual_day_type else None,
                    departure_time_local=loc.departure_time_local,
                    timezone=loc.timezone,
                    transport_mode=loc.transport_mode.value if loc.transport_mode else None,
                    confirmed_at=loc.confirmed_at,
                    is_complete=loc.is_complete,
                )
            )

        self.db.commit()
        self.db.refresh(orm)
        return orm

    def create_manual_listing_input(self, session_id: UUID) -> ListingInputORM:
        inp = ListingInputORM(
            session_id=session_id,
            input_method="manual",
            property_category=PropertyCategory.HDB.value,
            candidates_json={},
        )
        self.db.add(inp)
        self.db.commit()
        self.db.refresh(inp)
        return inp

    def get_listing_input(self, listing_input_id: UUID) -> ListingInputORM | None:
        return self.db.query(ListingInputORM).filter(ListingInputORM.id == listing_input_id).first()

    def has_duplicate_listing(self, session_id: UUID, address: str, asking_price: float) -> bool:
        """Check the same uniqueness boundary used by the database constraint."""
        return (
            self.db.query(ConfirmedListingORM.id)
            .filter(
                ConfirmedListingORM.session_id == session_id,
                ConfirmedListingORM.normalized_address_key == normalize_address(address),
                ConfirmedListingORM.asking_price == float(asking_price),
            )
            .first()
            is not None
        )

    def delete_unconfirmed_listing_input(self, listing_input_id: UUID) -> None:
        """Remove a manual/extraction input left behind by a failed confirmation."""
        listing_input = self.get_listing_input(listing_input_id)
        if listing_input and listing_input.confirmed_listing is None:
            self.db.delete(listing_input)
            self.db.commit()

    def confirm_listing(
        self,
        session_id: UUID,
        listing_input_id: UUID | None,
        data: dict,
    ) -> ConfirmedListingORM:
        session = self.get_session(session_id)
        if not session:
            raise ValueError("Session not found")
        if len(session.listings) >= 5:
            raise ValueError("Maximum five listings per session")

        address = data["address"]
        norm_key = normalize_address(address)
        raw_flat_type = str(data.get("flat_type_raw") or data["flat_type"])
        flat_type_attributes = normalize_flat_type(raw_flat_type)
        resolved_details = resolve_flat_property_details(
            flat_type=data.get("flat_type") or flat_type_attributes.flat_type,
            raw_listing_subtype=(
                data.get("raw_listing_subtype")
                or data.get("listing_flat_subtype")
                or flat_type_attributes.listing_flat_subtype
            ),
            flat_model=data.get("flat_model"),
            flat_type_source=data.get("flat_type_source") or "user_confirmed",
            flat_model_source=data.get("flat_model_source"),
            existing_conflicts=data.get("subtype_conflicts"),
        )
        flat_type = resolved_details.flat_type or str(data["flat_type"]).strip().upper()
        listing_flat_subtype = resolved_details.raw_listing_subtype
        raw_listing_subtype = resolved_details.raw_listing_subtype
        flat_model = resolved_details.flat_model
        flat_model_source = resolved_details.flat_model_source
        subtype_conflicts = resolved_details.subtype_conflicts
        provenance = [
            {
                "field_name": k,
                "value": data.get(k),
                "provenance": Provenance.USER_ENTERED.value,
                "status": DataStatus.AVAILABLE.value,
            }
            for k in ("asking_price", "floor_area_sqm", "address", "flat_type")
            if data.get(k) is not None
        ]
        raw_remaining_lease = data.get("remaining_lease_years")
        remaining_lease = (
            float(raw_remaining_lease) if raw_remaining_lease is not None and float(raw_remaining_lease) > 0 else None
        )
        remaining_lease_status = (
            data.get("remaining_lease_status", DataStatus.NOT_PROVIDED_BY_USER.value)
            if remaining_lease is not None
            else DataStatus.NOT_PROVIDED_BY_USER.value
        )
        remaining_lease_months = round(remaining_lease * 12) if remaining_lease is not None else None

        listing = ConfirmedListingORM(
            session_id=session_id,
            listing_input_id=listing_input_id,
            display_name=data.get("display_name") or address,
            asking_price=float(data["asking_price"]),
            floor_area_sqm=float(data["floor_area_sqm"]),
            address=address,
            normalized_address_key=norm_key,
            flat_type=flat_type,
            flat_type_raw=raw_flat_type,
            listing_flat_subtype=listing_flat_subtype,
            raw_listing_subtype=raw_listing_subtype,
            flat_type_source=resolved_details.flat_type_source,
            flat_model=flat_model,
            flat_model_source=flat_model_source,
            subtype_conflicts=subtype_conflicts,
            storey_range=data.get("storey_range"),
            storey_source=data.get("storey_source") or ("user" if data.get("storey_range") else None),
            property_category=data.get("property_category", PropertyCategory.HDB.value),
            renovation_estimate=data.get("renovation_estimate"),
            lease_commencement_year=data.get("lease_commencement_year"),
            remaining_lease_years=remaining_lease,
            remaining_lease_months=remaining_lease_months,
            # Listing/manual input is evidence supplied by the property
            # listing or user, not an official exact HDB value.
            remaining_lease_source=("listing_unverified" if remaining_lease_months is not None else None),
            remaining_lease_as_of_date=date.today() if remaining_lease_months is not None else None,
            remaining_lease_status=remaining_lease_status,
            source_url=data.get("source_url"),
            source_hash=content_hash(data.get("source_url") or address),
            field_provenance_json=provenance,
        )
        self.db.add(listing)
        self.db.commit()
        self.db.refresh(listing)
        return listing

    def get_listings(self, session_id: UUID) -> list[ConfirmedListing]:
        session = self.get_session(session_id)
        if not session:
            return []
        return [confirmed_listing_from_orm(l) for l in session.listings]

    def delete_listing(self, session_id: UUID, listing_id: UUID) -> bool:
        """Delete one shortlist listing and its listing-specific input evidence."""
        listing = (
            self.db.query(ConfirmedListingORM)
            .filter(
                ConfirmedListingORM.id == listing_id,
                ConfirmedListingORM.session_id == session_id,
            )
            .first()
        )
        if not listing:
            return False

        listing_input_id = listing.listing_input_id
        listing_input = (
            self.db.query(ListingInputORM).filter(ListingInputORM.id == listing_input_id).first()
            if listing_input_id
            else None
        )
        extraction_id = listing_input.extraction_id if listing_input else None

        # Child rows linked by on-delete CASCADE (observations, enrichment,
        # journeys) disappear with the confirmed listing. Listing inputs use
        # SET NULL by design, so remove the input explicitly here as well.
        if listing_input:
            self.db.delete(listing_input)
        self.db.delete(listing)
        self.db.flush()

        # An extraction attempt is listing-specific unless another input still
        # references it. Preserve it when it is shared by another input.
        if extraction_id:
            still_referenced = (
                self.db.query(ListingInputORM.id).filter(ListingInputORM.extraction_id == extraction_id).first()
            )
            if not still_referenced:
                extraction = self.db.get(ExtractionAttemptORM, extraction_id)
                if extraction:
                    self.db.delete(extraction)

        self.db.commit()
        return True

    def update_lease_estimate(
        self,
        listing_id: UUID,
        *,
        lease_commencement_year: int | None,
        remaining_lease_months: int | None,
        remaining_lease_years: float | None,
        remaining_lease_source: str,
        remaining_lease_confidence: str,
        remaining_lease_as_of_date: date,
        remaining_lease_status: DataStatus,
    ) -> None:
        """Persist the canonical lease result produced during enrichment."""
        listing = self.db.query(ConfirmedListingORM).filter(ConfirmedListingORM.id == listing_id).first()
        if not listing:
            raise ValueError("Listing not found")

        listing.lease_commencement_year = lease_commencement_year
        listing.remaining_lease_months = remaining_lease_months
        listing.remaining_lease_years = remaining_lease_years
        listing.remaining_lease_source = remaining_lease_source
        listing.remaining_lease_confidence = remaining_lease_confidence
        listing.remaining_lease_as_of_date = remaining_lease_as_of_date
        listing.remaining_lease_status = remaining_lease_status.value
        self.db.commit()

    def get_buyer_profile(self, session_id: UUID) -> BuyerProfile | None:
        session = self.get_session(session_id)
        if not session or not session.buyer_profile:
            return None
        return buyer_profile_from_orm(session.buyer_profile)

    def save_recommendation_trace(self, session_id: UUID, trace_json: dict, inputs_hash: str) -> None:
        row = RecommendationTraceORM(
            session_id=session_id,
            trace_json=trace_json,
            inputs_hash=inputs_hash,
            rule_version=settings.recommendation_rule_version,
            scoring_version=settings.scoring_version,
        )
        self.db.add(row)
        self.db.commit()

    def get_latest_recommendation_trace(self, session_id: UUID) -> dict | None:
        row = (
            self.db.query(RecommendationTraceORM)
            .filter(RecommendationTraceORM.session_id == session_id)
            .order_by(RecommendationTraceORM.created_at.desc())
            .first()
        )
        if not row:
            return None
        return {
            "trace_id": str(row.id),
            "trace_json": row.trace_json,
            "inputs_hash": row.inputs_hash,
            "rule_version": row.rule_version,
            "scoring_version": row.scoring_version,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def listing_input_domain(self, listing_input_id: UUID):
        orm = self.get_listing_input(listing_input_id)
        return listing_input_from_orm(orm) if orm else None
