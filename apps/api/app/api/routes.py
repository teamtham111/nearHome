"""API route handlers."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from redis import Redis
from redis.exceptions import RedisError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import AdapterError
from app.adapters.factory import get_places_adapter
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.domain.enums import PriorityType
from app.domain.models import (
    BuyerProfile,
    HardRequirement,
    ImportantLocation,
    build_priorities,
)
from app.repositories.session_repository import SessionRepository
from app.schemas.comparison import (
    BuyerProfileInput,
    ComparisonResponse,
    ConfirmListingFromInput,
    EnrichmentStatusResponse,
    GoogleRoutesDiagnosticRequest,
    HealthResponse,
    ManualListingInput,
    ObservationCreate,
    PlacesAutocompleteResponse,
    SessionCreateResponse,
    SmartPasteInput,
)
from app.services.comparison_service import ComparisonService
from app.services.enrichment_service import EnrichmentService
from app.services.observation_service import ObservationService
from app.services.smart_paste.retrieval import ListingRetrievalError
from app.services.smart_paste.service import SmartPasteService

router = APIRouter(prefix="/api/v1")
logger = get_logger(__name__)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", demo_mode=settings.demo_mode, checks={"api": "ok"})


@router.get("/ready", response_model=HealthResponse)
def ready(db: Session = Depends(get_db)) -> JSONResponse:
    """Report dependency state without disclosing connection details.

    PostgreSQL is required, while Redis remains an optional queue/cache
    dependency because the app has an inline enrichment fallback.
    """
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception:
        logger.exception("readiness_database_failed", error_category="database")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=HealthResponse(
                status="unavailable",
                demo_mode=settings.demo_mode,
                checks={"database": "unavailable", "redis": "unknown"},
            ).model_dump(),
        )

    try:
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        redis.ping()
        redis.close()
    except RedisError:
        logger.warning("readiness_redis_degraded", error_category="redis")
        return JSONResponse(
            content=HealthResponse(
                status="degraded",
                demo_mode=settings.demo_mode,
                checks={"database": "ok", "redis": "unavailable"},
            ).model_dump(),
        )

    return JSONResponse(
        content=HealthResponse(
            status="ready",
            demo_mode=settings.demo_mode,
            checks={"database": "ok", "redis": "ok"},
        ).model_dump()
    )


@router.post("/diagnostics/google-routes")
def diagnose_google_routes(body: GoogleRoutesDiagnosticRequest = GoogleRoutesDiagnosticRequest()) -> JSONResponse:
    """Probe the primary Google Routes provider without the normal mock fallback.

    This endpoint is deliberately development-only. It returns a structured
    provider diagnostic while the adapter logs the complete response body;
    production traffic never receives Google's raw error details.
    """
    if settings.app_env != "development":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    from app.adapters.routing.base import RoutingProviderError
    from app.adapters.routing.google import GoogleRoutingProvider

    provider = GoogleRoutingProvider()
    origin, destination = body.origin, body.destination
    try:
        if body.travel_mode == "DRIVE":
            route = provider.get_driving_route(origin, destination, datetime.now(UTC), traffic_aware=False)
        elif body.travel_mode == "WALK":
            route = provider.get_walking_route(origin, destination)
        else:
            route = provider.get_transit_route(origin, destination, datetime.now(UTC))
    except RoutingProviderError as exc:
        return JSONResponse(
            content={
                "success": False,
                "provider": "google-routes",
                "httpStatus": exc.http_status,
                "errorCode": exc.error_code,
                "message": str(exc),
                "requestId": exc.request_id,
            }
        )

    return JSONResponse(
        content={
            "success": True,
            "provider": "google-routes",
            "httpStatus": 200,
            "route": {
                "durationMinutes": route.duration_minutes,
                "distanceMetres": route.distance_metres,
            },
        }
    )


@router.post("/sessions", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED)
def create_session(db: Session = Depends(get_db)) -> SessionCreateResponse:
    repo = SessionRepository(db)
    session = repo.create_session()
    return SessionCreateResponse(
        session_id=session.id,
        demo_mode=session.demo_mode,
        created_at=session.created_at,
    )


@router.get("/sessions/{session_id}")
def get_session(session_id: UUID, db: Session = Depends(get_db)) -> dict:
    repo = SessionRepository(db)
    session = repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    profile = repo.get_buyer_profile(session_id)
    listings = repo.get_listings(session_id)
    return {
        "session_id": session.id,
        "demo_mode": session.demo_mode,
        "profile_saved": profile is not None,
        "buyer_profile": {
            "max_budget": profile.max_budget,
            "main_transport_mode": profile.main_transport_mode.value,
            "schools_matter": profile.schools_matter,
            "named_schools": profile.school_names,
            "named_school": profile.named_school,
            "priorities": [p.priority_type.value for p in profile.priorities],
            "important_locations": [
                {
                    "important_location_id": str(location.important_location_id),
                    "label": location.label,
                    "place_id": location.place_id,
                    "formatted_address": location.formatted_address,
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "usual_day_type": location.usual_day_type.value if location.usual_day_type else None,
                    "departure_time_local": location.departure_time_local.isoformat()
                    if location.departure_time_local
                    else None,
                    "transport_mode": location.transport_mode.value if location.transport_mode else None,
                    "is_complete": location.is_complete,
                }
                for location in profile.important_locations
            ],
        }
        if profile
        else None,
        "listings": [
            {
                "listing_id": str(l.listing_id),
                "display_name": l.display_name,
                "address": l.address,
                "asking_price": l.asking_price,
                "floor_area_sqm": l.floor_area_sqm,
                "flat_type": l.flat_type,
                "flat_type_raw": l.flat_type_raw,
                "listing_flat_subtype": l.listing_flat_subtype,
                "raw_listing_subtype": l.raw_listing_subtype or l.listing_flat_subtype,
                "flat_type_source": l.flat_type_source,
                "flat_model": l.flat_model,
                "flat_model_source": l.flat_model_source,
                "subtype_conflicts": l.subtype_conflicts,
                "storey_range": l.storey_range,
                "storey_source": l.storey_source,
                "lease_commencement_year": l.lease_commencement_year,
                "remaining_lease_months": l.remaining_lease_months,
                "remaining_lease_years": l.remaining_lease_years,
                "remaining_lease_source": l.remaining_lease_source,
                "remaining_lease_confidence": l.remaining_lease_confidence,
                "remaining_lease_as_of_date": (
                    l.remaining_lease_as_of_date.isoformat() if l.remaining_lease_as_of_date else None
                ),
            }
            for l in listings
        ],
        "listing_count": len(listings),
    }


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: UUID, db: Session = Depends(get_db)) -> None:
    repo = SessionRepository(db)
    if not repo.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")


@router.delete("/sessions/{session_id}/listings/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_listing(session_id: UUID, listing_id: UUID, db: Session = Depends(get_db)) -> None:
    repo = SessionRepository(db)
    if not repo.delete_listing(session_id, listing_id):
        raise HTTPException(status_code=404, detail="Listing not found in this session")


@router.put("/sessions/{session_id}/buyer-profile")
def upsert_buyer_profile(
    session_id: UUID,
    body: BuyerProfileInput,
    db: Session = Depends(get_db),
) -> dict:
    repo = SessionRepository(db)
    if not repo.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    loc_ids: dict[str, UUID] = {}
    important_locations: list[ImportantLocation] = []
    for loc_in in body.important_locations:
        loc_id = uuid4()
        loc_ids[loc_in.label] = loc_id
        is_complete = all(
            [
                loc_in.place_id,
                loc_in.formatted_address,
                loc_in.latitude is not None,
                loc_in.longitude is not None,
                loc_in.usual_day_type,
                loc_in.departure_time_local,
                loc_in.transport_mode,
            ]
        )
        important_locations.append(
            ImportantLocation(
                important_location_id=loc_id,
                label=loc_in.label,
                place_id=loc_in.place_id,
                formatted_address=loc_in.formatted_address,
                latitude=loc_in.latitude,
                longitude=loc_in.longitude,
                usual_day_type=loc_in.usual_day_type,
                departure_time_local=loc_in.departure_time_local,
                transport_mode=loc_in.transport_mode,
                is_complete=bool(is_complete),
            )
        )

    priority_specs = []
    for p in body.priorities:
        loc_id = p.important_location_id
        if p.priority_type == PriorityType.IMPORTANT_LOCATION_JOURNEY and loc_id is None:
            raise HTTPException(
                status_code=400,
                detail="Important location journey priority requires important_location_id",
            )
        priority_specs.append((p.priority_type, loc_id))

    priorities = build_priorities(priority_specs)

    profile = BuyerProfile(
        max_budget=body.max_budget,
        priorities=priorities,
        main_transport_mode=body.main_transport_mode,
        hard_requirements=[
            HardRequirement(
                requirement_id=None,
                metric=hr.metric,
                operator=hr.operator,
                threshold=hr.threshold,
                unit=hr.unit,
                label=hr.label,
                important_location_id=hr.important_location_id,
            )
            for hr in body.hard_requirements
        ],
        important_locations=important_locations,
        schools_matter=body.schools_matter,
        named_schools=body.named_schools,
        named_school=body.named_school,
    )

    try:
        from app.engines.requirement_engine import RequirementEngine

        for hr in profile.hard_requirements:
            RequirementEngine.validate_requirement(hr)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    repo.upsert_buyer_profile(session_id, profile)
    return {"status": "saved", "session_id": session_id}


@router.post("/sessions/{session_id}/listings/manual", status_code=status.HTTP_201_CREATED)
def create_manual_listing(
    session_id: UUID,
    body: ManualListingInput,
    db: Session = Depends(get_db),
) -> dict:
    repo = SessionRepository(db)
    if not repo.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    if repo.has_duplicate_listing(session_id, body.address, body.asking_price):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This address and asking price are already in your shortlist.",
        )

    listing_input = repo.create_manual_listing_input(session_id)
    try:
        listing = repo.confirm_listing(session_id, listing_input.id, body.model_dump())
    except IntegrityError as exc:
        db.rollback()
        repo.delete_unconfirmed_listing_input(listing_input.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This address and asking price are already in your shortlist.",
        ) from exc
    return {
        "listing_id": listing.id,
        "listing_input_id": listing_input.id,
        "display_name": listing.display_name,
    }


@router.get("/sessions/{session_id}/comparison", response_model=ComparisonResponse)
def get_comparison(session_id: UUID, db: Session = Depends(get_db)) -> ComparisonResponse:
    service = ComparisonService(db)
    try:
        return service.get_comparison(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/sessions/{session_id}/enrichment/start")
async def start_enrichment(session_id: UUID, db: Session = Depends(get_db)) -> dict:
    if not SessionRepository(db).get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    from app.jobs.queue import enqueue_enrichment

    return await enqueue_enrichment(session_id)


@router.get("/sessions/{session_id}/enrichment/status", response_model=EnrichmentStatusResponse)
def enrichment_status(session_id: UUID, db: Session = Depends(get_db)) -> EnrichmentStatusResponse:
    service = EnrichmentService(db)
    if not SessionRepository(db).get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return EnrichmentStatusResponse(session_id=session_id, runs=service.get_status(session_id))


@router.get("/places/autocomplete", response_model=PlacesAutocompleteResponse)
def places_autocomplete(q: str) -> PlacesAutocompleteResponse:
    adapter = get_places_adapter()
    try:
        suggestions = adapter.autocomplete(q)
    except (AdapterError, httpx.HTTPError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Live location search is temporarily unavailable. Please retry.",
        ) from exc
    return PlacesAutocompleteResponse(
        suggestions=[
            {"place_id": s.place_id, "description": s.description, "main_text": s.main_text} for s in suggestions
        ]
    )


@router.get("/places/{place_id}")
def place_details(place_id: str) -> dict:
    adapter = get_places_adapter()
    try:
        details = adapter.get_details(place_id)
    except (AdapterError, httpx.HTTPError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Live location details are temporarily unavailable. Please retry.",
        ) from exc
    return {
        "place_id": details.place_id,
        "formatted_address": details.formatted_address,
        "latitude": details.latitude,
        "longitude": details.longitude,
        "provider": details.provider,
    }


@router.post("/sessions/{session_id}/smart-paste", status_code=status.HTTP_201_CREATED)
def smart_paste(session_id: UUID, body: SmartPasteInput, db: Session = Depends(get_db)) -> dict:
    if not SessionRepository(db).get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    service = SmartPasteService(db)
    source_type = body.source_type or ("url" if body.source_url else "text")
    source_text = body.raw_text or body.text or ""
    try:
        listing_input, used_fallback = service.extract(
            session_id,
            source_text,
            body.source_label,
            body.source_url,
            source_type,
        )
    except ListingRetrievalError as exc:
        logger.warning(
            "SMART_PASTE_FAILURE",
            stage="page_retrieval",
            error_code=exc.code,
            provider_status=exc.status_code,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except AdapterError as e:
        response_status = {
            "rate_limit": 429,
            "configuration": 503,
            "authentication": 502,
            "timeout": 504,
            "temporarily_unavailable": 503,
            "invalid_output": 502,
            "empty_response": 502,
        }.get(e.error_code, 502)
        if settings.app_env == "development":
            raise HTTPException(
                status_code=response_status,
                detail={
                    "success": False,
                    "provider": "groq-smart-paste",
                    "httpStatus": (
                        int(e.provider_status) if e.provider_status and e.provider_status.isdigit() else response_status
                    ),
                    "errorCode": e.error_code,
                    "message": str(e),
                    "providerMessage": e.provider_message,
                },
            ) from e
        raise HTTPException(status_code=response_status, detail=str(e)) from e

    from app.services.smart_paste.flat_attributes import (
        flat_type_source,
        normalise_listing_subtype,
        normalize_flat_type,
    )
    from app.services.smart_paste.reconciliation import reconcile_candidates

    suggested, reconcile_warnings, evidence = reconcile_candidates(listing_input.candidates)
    if not suggested:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EXTRACTION_EMPTY",
                "message": (
                    "The listing was retrieved, but no supported fields could be identified. "
                    "Copy and paste the listing text instead."
                ),
            },
        )
    raw_subtype = suggested.get("listing_flat_subtype")
    subtype_result = normalise_listing_subtype(raw_subtype if isinstance(raw_subtype, str) else None)
    if isinstance(raw_subtype, str) and raw_subtype.strip():
        suggested["raw_listing_subtype"] = raw_subtype
        if subtype_result.flat_type and "flat_type" not in suggested:
            suggested["flat_type"] = subtype_result.flat_type
            evidence.setdefault("flat_type", []).append(
                {
                    "value": subtype_result.flat_type,
                    "raw_text": raw_subtype,
                    "source_snippet": raw_subtype,
                    "source_section": "listing_subtype",
                    "extraction_method": "derived_from_subtype",
                    "final_confidence": "HIGH",
                    "status": "AVAILABLE",
                }
            )
        if subtype_result.flat_model and "flat_model" not in suggested:
            suggested["flat_model"] = subtype_result.flat_model
            evidence.setdefault("flat_model", []).append(
                {
                    "value": subtype_result.flat_model,
                    "raw_text": raw_subtype,
                    "source_snippet": raw_subtype,
                    "source_section": "listing_subtype",
                    "extraction_method": "derived_from_subtype",
                    "final_confidence": "HIGH",
                    "status": "AVAILABLE",
                }
            )
        elif subtype_result.flat_model and isinstance(suggested.get("flat_model"), str):
            explicit_model_evidence = [
                item for item in evidence.get("flat_model", [])
                if item.get("extraction_method") != "derived_from_subtype"
            ]
            normalized_explicit = " ".join(suggested["flat_model"].upper().split())
            normalized_derived = " ".join(subtype_result.flat_model.upper().split())
            if explicit_model_evidence and normalized_explicit != normalized_derived:
                reconcile_warnings.append(
                    f"Flat model conflict: extracted {suggested['flat_model']!r}, "
                    f"while subtype {raw_subtype!r} deterministically maps to {subtype_result.flat_model!r}. "
                    "Review before confirming."
                )
                suggested["subtype_conflicts"] = [{
                    "field": "flat_model",
                    "confirmed_value": suggested["flat_model"],
                    "derived_from_subtype": subtype_result.flat_model,
                    "raw_listing_subtype": raw_subtype,
                    "status": "conflict",
                }]
    elif isinstance(suggested.get("flat_type"), str):
        type_attributes = normalize_flat_type(suggested["flat_type"])
        if type_attributes.listing_flat_subtype:
            suggested["raw_listing_subtype"] = type_attributes.listing_flat_subtype

    missing_fields = [
        field for field in ("address", "asking_price", "floor_area_sqm", "flat_type") if field not in suggested
    ]
    if missing_fields:
        reconcile_warnings.append(
            "The listing was retrieved, but these fields could not be identified: "
            + ", ".join(missing_fields)
            + ". Complete them manually before confirming."
        )

    if "flat_type" in suggested:
        flat_evidence = evidence.get("flat_type", [])
        suggested["flat_type_raw"] = (
            flat_evidence[0].get("raw_text")
            if flat_evidence and flat_evidence[0].get("raw_text")
            else suggested["flat_type"]
        )

    field_sources = {}
    for field in ("flat_type", "listing_flat_subtype", "raw_listing_subtype"):
        field_evidence = evidence.get(field, [])
        section = field_evidence[0].get("source_section") if field_evidence else None
        field_sources[field] = flat_type_source(source_type, section) if field != "raw_listing_subtype" else (
            flat_type_source(source_type, section) if section else "listing_text"
        )
    if "flat_model" in suggested:
        model_evidence = evidence.get("flat_model", [])
        field_sources["flat_model"] = (
            "derived_from_subtype"
            if any(item.get("extraction_method") == "derived_from_subtype" for item in model_evidence)
            else "listing_structured_data"
        )

    if settings.app_env == "development":
        logger.info("SMART_PASTE_RESPONSE_RETURNED", source_type=source_type, field_count=len(suggested))

    return {
        "listing_input_id": str(listing_input.listing_input_id),
        "extraction_id": str(listing_input.extraction_id) if listing_input.extraction_id else None,
        "llm_fallback": used_fallback,
        "suggested_values": suggested,
        "evidence_by_field": evidence,
        "field_sources": field_sources,
        "candidates": {
            k: [
                {
                    "value": c.value,
                    "raw_text": c.raw_text,
                    "source_snippet": c.source_snippet,
                    "extraction_method": c.extraction_method,
                    "final_confidence": c.final_confidence.value,
                    "status": c.status.value,
                }
                for c in v
            ]
            for k, v in listing_input.candidates.items()
        },
        "extraction_warnings": listing_input.extraction_warnings + reconcile_warnings,
        "agent_claims": listing_input.agent_claims,
        "property_category": listing_input.property_category.value,
        "sourceType": source_type,
        "sourceUrl": listing_input.source_url,
    }


@router.get("/listing-inputs/{listing_input_id}")
def get_listing_input(listing_input_id: UUID, db: Session = Depends(get_db)) -> dict:
    service = SmartPasteService(db)
    inp = service.get_listing_input(listing_input_id)
    if not inp:
        raise HTTPException(status_code=404, detail="Listing input not found")
    return {
        "listing_input_id": str(inp.listing_input_id),
        "raw_text": inp.raw_text,
        "candidates": {
            k: [{"value": c.value, "source_snippet": c.source_snippet, "status": c.status.value} for c in v]
            for k, v in inp.candidates.items()
        },
        "extraction_warnings": inp.extraction_warnings,
        "agent_claims": inp.agent_claims,
    }


@router.delete("/sessions/{session_id}/listing-inputs/{listing_input_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_listing_input(session_id: UUID, listing_input_id: UUID, db: Session = Depends(get_db)) -> None:
    """Discard an unconfirmed Smart Paste/manual input owned by this session."""
    repo = SessionRepository(db)
    if not repo.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    listing_input = repo.get_listing_input(listing_input_id)
    if not listing_input or listing_input.session_id != session_id:
        raise HTTPException(status_code=404, detail="Listing input not found")
    if listing_input.confirmed_listing is not None:
        raise HTTPException(status_code=409, detail="Confirmed listings cannot be discarded as extraction drafts")
    repo.delete_unconfirmed_listing_input(listing_input_id)


@router.post("/sessions/{session_id}/listings/confirm", status_code=status.HTTP_201_CREATED)
def confirm_from_input(
    session_id: UUID,
    body: ConfirmListingFromInput,
    db: Session = Depends(get_db),
) -> dict:
    repo = SessionRepository(db)
    if not repo.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    if repo.has_duplicate_listing(session_id, body.address, body.asking_price):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This address and asking price are already in your shortlist.",
        )
    data = body.model_dump()
    listing_input_id = data.pop("listing_input_id")
    try:
        listing = repo.confirm_listing(session_id, listing_input_id, data)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This address and asking price are already in your shortlist.",
        ) from exc
    return {"listing_id": str(listing.id), "display_name": listing.display_name}


@router.get("/sessions/{session_id}/recommendation-trace")
def get_recommendation_trace(session_id: UUID, db: Session = Depends(get_db)) -> dict:
    repo = SessionRepository(db)
    if not repo.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    trace = repo.get_latest_recommendation_trace(session_id)
    if not trace:
        raise HTTPException(status_code=404, detail="No recommendation trace yet — run comparison first")
    return trace


@router.get("/sessions/{session_id}/observations")
def list_observations(session_id: UUID, db: Session = Depends(get_db)) -> dict:
    if not SessionRepository(db).get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"observations": ObservationService(db).list_for_session(session_id)}


@router.post("/listings/{listing_id}/observations", status_code=status.HTTP_201_CREATED)
def create_observation(listing_id: UUID, body: ObservationCreate, db: Session = Depends(get_db)) -> dict:
    service = ObservationService(db)
    try:
        return service.create(listing_id, body.category, body.value_text)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/observations/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_observation(observation_id: UUID, db: Session = Depends(get_db)) -> None:
    if not ObservationService(db).delete(observation_id):
        raise HTTPException(status_code=404, detail="Observation not found")
