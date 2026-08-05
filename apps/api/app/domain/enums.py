"""Explicit domain enums — every displayed value traces to one of these states."""

from enum import StrEnum


class DataStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_PROVIDED_BY_USER = "NOT_PROVIDED_BY_USER"
    NOT_FOUND_IN_SOURCE_TEXT = "NOT_FOUND_IN_SOURCE_TEXT"
    UNAVAILABLE_FROM_OFFICIAL_SOURCE = "UNAVAILABLE_FROM_OFFICIAL_SOURCE"
    EXTRACTION_UNCERTAIN = "EXTRACTION_UNCERTAIN"
    CONFLICTING_VALUES = "CONFLICTING_VALUES"
    CALCULATION_FAILED = "CALCULATION_FAILED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REQUIRES_VERIFICATION = "REQUIRES_VERIFICATION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ScoreStatus(StrEnum):
    CALCULATED = "calculated"
    PARTIAL = "partial"
    MISSING_INPUT = "missing_input"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"
    # A component/model that was explicitly evaluated but for which the
    # required source data does not exist — distinct from PARTIAL (some data)
    # or MISSING_INPUT (no coordinates at all). Never carries a numeric score.
    NOT_ASSESSED = "not_assessed"


class ComponentStatus(StrEnum):
    """Per-component status for the rebuilt Public Transport / Driving models.

    Mirrors the vocabulary requested by the transport/driving spec. Kept
    separate from ScoreStatus (which describes the *overall* model rollup)
    so a component can be "not_assessed" while the overall model is
    "partially_assessed" from other components.
    """

    CALCULATED = "calculated"
    ESTIMATED = "estimated"
    PARTIALLY_ASSESSED = "partially_assessed"
    NOT_ASSESSED = "not_assessed"
    PROVIDER_ERROR = "provider_error"
    INSUFFICIENT_DATA = "insufficient_data"


class Provenance(StrEnum):
    USER_ENTERED = "USER_ENTERED"
    EXTRACTED_LLM = "EXTRACTED_LLM"
    USER_CORRECTED = "USER_CORRECTED"
    RULE_VALIDATION_CANDIDATE = "RULE_VALIDATION_CANDIDATE"
    OFFICIAL = "OFFICIAL"
    PROVIDER_ESTIMATED = "PROVIDER_ESTIMATED"
    INFERRED = "INFERRED"
    CALCULATED = "CALCULATED"
    UNVERIFIED_CLAIM = "UNVERIFIED_CLAIM"
    MOCK_DEMO_DATA = "MOCK_DEMO_DATA"
    # Value came from a live routing-provider call (Google Routes), i.e. an
    # actual routed walking/driving/transit result, not a Haversine estimate.
    ROUTED_LIVE = "ROUTED_LIVE"
    # Value came from a curated-but-real structural reference dataset (rail
    # graph topology, road access points) that is not a live feed — distinct
    # from MOCK_DEMO_DATA, which is intentionally fake demo content.
    CURATED_REFERENCE_DATA = "CURATED_REFERENCE_DATA"
    UNAVAILABLE = "unavailable"


class RequirementStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    CANNOT_DETERMINE = "CANNOT_DETERMINE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class JourneyMode(StrEnum):
    PUBLIC_TRANSPORT = "PUBLIC_TRANSPORT"
    DRIVING = "DRIVING"
    BOTH = "BOTH"


class DayType(StrEnum):
    WEEKDAY = "WEEKDAY"
    WEEKEND = "WEEKEND"


class MainTransportMode(StrEnum):
    MAINLY_PUBLIC_TRANSPORT = "MAINLY_PUBLIC_TRANSPORT"
    MAINLY_DRIVING = "MAINLY_DRIVING"
    BOTH = "BOTH"


class PriorityType(StrEnum):
    AFFORDABILITY = "AFFORDABILITY"
    SPACE = "SPACE"
    LEASE = "LEASE"
    PUBLIC_TRANSPORT = "PUBLIC_TRANSPORT"
    DRIVING = "DRIVING"
    FAIR_PRICE = "FAIR_PRICE"
    SCHOOLS = "SCHOOLS"
    IMPORTANT_LOCATION_JOURNEY = "IMPORTANT_LOCATION_JOURNEY"


class RequirementMetric(StrEnum):
    REMAINING_LEASE_YEARS = "REMAINING_LEASE_YEARS"
    FLOOR_AREA_SQM = "FLOOR_AREA_SQM"
    FLAT_TYPE = "FLAT_TYPE"
    MAX_DRIVING_JOURNEY_MINUTES = "MAX_DRIVING_JOURNEY_MINUTES"


class RequirementOperator(StrEnum):
    LTE = "LTE"
    GTE = "GTE"
    EQ = "EQ"


# Metrics explicitly rejected by the requirement registry
REJECTED_REQUIREMENT_METRICS = frozenset(
    {
        "journey_duration_seconds",
        "commute_minutes",
        "important_location_travel_time",
        "JOURNEY_DURATION_SECONDS",
        "COMMUTE_MINUTES",
        "IMPORTANT_LOCATION_TRAVEL_TIME",
    }
)


class ListingGroup(StrEnum):
    PASSES_ALL = "PASSES_ALL"
    CANNOT_DETERMINE = "CANNOT_DETERMINE"
    FAILS_ONE = "FAILS_ONE"
    FAILS_MULTIPLE = "FAILS_MULTIPLE"


class EnrichmentStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED = "SKIPPED"


class EnrichmentType(StrEnum):
    GEOCODING = "GEOCODING"
    PROPERTY_DATA = "PROPERTY_DATA"
    TRANSACTION_DATA = "TRANSACTION_DATA"
    LEASE = "LEASE"
    SCHOOLS = "SCHOOLS"
    PUBLIC_TRANSPORT = "PUBLIC_TRANSPORT"
    IMPORTANT_LOCATION_PT = "IMPORTANT_LOCATION_PT"
    IMPORTANT_LOCATION_DRIVING = "IMPORTANT_LOCATION_DRIVING"
    DRIVING_ACCESS = "DRIVING_ACCESS"
    HOME_PARKING = "HOME_PARKING"
    FAIR_PRICE = "FAIR_PRICE"


class PropertyCategory(StrEnum):
    HDB = "HDB"
    NON_HDB = "NON_HDB"
    UNKNOWN = "UNKNOWN"


class VerificationState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    USER_CONFIRMED = "USER_CONFIRMED"
    OFFICIALLY_VERIFIED = "OFFICIALLY_VERIFIED"


class RecommendationConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    PROVISIONAL = "PROVISIONAL"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"
