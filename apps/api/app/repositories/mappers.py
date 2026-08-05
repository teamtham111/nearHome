"""Map between ORM rows and domain objects."""

from __future__ import annotations

from uuid import UUID

from app.domain.enums import (
    ConfidenceLevel,
    DataStatus,
    DayType,
    JourneyMode,
    MainTransportMode,
    PriorityType,
    PropertyCategory,
    Provenance,
    RequirementMetric,
    RequirementOperator,
    VerificationState,
)
from app.domain.models import (
    BuyerProfile,
    ConfirmedListing,
    FieldCandidate,
    HardRequirement,
    ImportantLocation,
    ListingFieldProvenance,
    ListingInput,
    Priority,
)
from app.models.orm import (
    BuyerProfileORM,
    ConfirmedListingORM,
    HardRequirementORM,
    ImportantLocationORM,
    ListingInputORM,
)


def priority_from_json(item: dict) -> Priority:
    return Priority(
        priority_type=PriorityType(item["priority_type"]),
        rank=item["rank"],
        weight=item["weight"],
        important_location_id=UUID(item["important_location_id"]) if item.get("important_location_id") else None,
    )


def buyer_profile_from_orm(orm: BuyerProfileORM) -> BuyerProfile:
    named_schools = [str(value).strip() for value in (orm.named_schools_json or []) if str(value).strip()]
    if not named_schools and orm.named_school:
        named_schools = [orm.named_school.strip()]
    return BuyerProfile(
        max_budget=orm.max_budget,
        priorities=[priority_from_json(p) for p in (orm.priorities_json or [])],
        main_transport_mode=MainTransportMode(orm.main_transport_mode),
        hard_requirements=[hard_requirement_from_orm(h) for h in orm.hard_requirements],
        important_locations=[important_location_from_orm(loc) for loc in orm.important_locations],
        schools_matter=orm.schools_matter,
        named_schools=named_schools,
        named_school=named_schools[0] if named_schools else None,
    )


def hard_requirement_from_orm(orm: HardRequirementORM) -> HardRequirement:
    threshold: float | str = orm.threshold_text if orm.threshold_text is not None else orm.threshold_number or 0.0
    return HardRequirement(
        requirement_id=orm.id,
        metric=RequirementMetric(orm.metric),
        operator=RequirementOperator(orm.operator),
        threshold=threshold,
        unit=orm.unit,
        label=orm.label,
        important_location_id=orm.important_location_id,
    )


def important_location_from_orm(orm: ImportantLocationORM) -> ImportantLocation:
    return ImportantLocation(
        important_location_id=orm.id,
        label=orm.label,
        place_id=orm.place_id,
        formatted_address=orm.formatted_address,
        latitude=orm.latitude,
        longitude=orm.longitude,
        usual_day_type=DayType(orm.usual_day_type) if orm.usual_day_type else None,
        departure_time_local=orm.departure_time_local,
        timezone=orm.timezone,
        transport_mode=JourneyMode(orm.transport_mode) if orm.transport_mode else None,
        confirmed_at=orm.confirmed_at,
        is_complete=orm.is_complete,
    )


def confirmed_listing_from_orm(orm: ConfirmedListingORM) -> ConfirmedListing:
    provenance = [
        ListingFieldProvenance(
            field_name=p["field_name"],
            value=p.get("value"),
            provenance=Provenance(p.get("provenance", "USER_ENTERED")),
            status=DataStatus(p.get("status", "AVAILABLE")),
            source_snippet=p.get("source_snippet"),
            corrected_by_user=p.get("corrected_by_user", False),
        )
        for p in (orm.field_provenance_json or [])
    ]
    return ConfirmedListing(
        listing_id=orm.id,
        session_id=orm.session_id,
        display_name=orm.display_name,
        asking_price=orm.asking_price,
        floor_area_sqm=orm.floor_area_sqm,
        address=orm.address,
        flat_type=orm.flat_type,
        flat_type_raw=orm.flat_type_raw,
        listing_flat_subtype=orm.listing_flat_subtype,
        raw_listing_subtype=orm.raw_listing_subtype or orm.listing_flat_subtype,
        flat_type_source=orm.flat_type_source,
        property_category=PropertyCategory(orm.property_category),
        renovation_estimate=orm.renovation_estimate,
        lease_commencement_year=orm.lease_commencement_year,
        remaining_lease_years=orm.remaining_lease_years,
        remaining_lease_months=(
            orm.remaining_lease_months
            if orm.remaining_lease_months is not None
            else round(orm.remaining_lease_years * 12)
            if orm.remaining_lease_years is not None
            else None
        ),
        remaining_lease_source=orm.remaining_lease_source,
        remaining_lease_confidence=orm.remaining_lease_confidence,
        remaining_lease_as_of_date=orm.remaining_lease_as_of_date,
        remaining_lease_status=DataStatus(orm.remaining_lease_status),
        source_url=orm.source_url,
        confirmed_at=orm.confirmed_at,
        field_provenance=provenance,
        listing_input_id=orm.listing_input_id,
        flat_model=orm.flat_model,
        flat_model_source=orm.flat_model_source,
        subtype_conflicts=orm.subtype_conflicts or [],
        storey_range=orm.storey_range,
        storey_source=orm.storey_source,
    )


def listing_input_from_orm(orm: ListingInputORM) -> ListingInput:
    candidates: dict[str, list[FieldCandidate]] = {}
    for field_name, items in (orm.candidates_json or {}).items():
        candidates[field_name] = [
            FieldCandidate(
                value=item.get("value"),
                raw_text=item.get("raw_text"),
                source_snippet=item.get("source_snippet"),
                source_section=item.get("source_section"),
                extraction_method=item.get("extraction_method", "manual"),
                model_confidence=item.get("model_confidence"),
                final_confidence=ConfidenceLevel(item.get("final_confidence", "NONE")),
                verification_state=VerificationState(item.get("verification_state", "UNVERIFIED")),
                status=DataStatus(item.get("status", "NOT_PROVIDED_BY_USER")),
                conflicting_candidates=item.get("conflicting_candidates", []),
            )
            for item in items
        ]
    return ListingInput(
        listing_input_id=orm.id,
        session_id=orm.session_id,
        extraction_id=orm.extraction_id,
        raw_text=orm.raw_text,
        cleaned_text=orm.cleaned_text,
        candidates=candidates,
        extraction_warnings=orm.extraction_warnings or [],
        agent_claims=orm.agent_claims or [],
        source_label=orm.source_label,
        source_url=orm.source_url,
        property_category=PropertyCategory(orm.property_category),
        pipeline_version=orm.pipeline_version,
        model_name=orm.model_name,
        model_version=orm.model_version,
        prompt_version=orm.prompt_version,
        schema_version=orm.schema_version,
        input_method=orm.input_method,
    )
