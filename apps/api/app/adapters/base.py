"""Shared adapter types and errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class AdapterError(Exception):
    def __init__(
        self,
        message: str,
        provider_status: str | None = None,
        error_code: str = "provider_error",
        retryable: bool = False,
        provider_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_status = provider_status
        self.error_code = error_code
        self.retryable = retryable
        self.provider_message = provider_message


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str
    postal_code: str | None
    town: str | None
    block: str | None
    street: str | None
    provider: str
    provenance: str
    retrieved_at: datetime


@dataclass
class PlaceSuggestion:
    place_id: str
    description: str
    main_text: str


@dataclass
class PlaceDetails:
    place_id: str
    formatted_address: str
    latitude: float
    longitude: float
    provider: str


@dataclass
class RouteMatrixElement:
    origin_index: int
    duration_seconds: int | None
    status: str
    provider_status: str | None = None


@dataclass
class RouteMatrixResult:
    elements: list[RouteMatrixElement]
    provider: str
    retrieved_at: datetime
    resolved_departure_at: datetime


@dataclass
class TransactionRecord:
    transaction_id: str
    transaction_month: str
    town: str
    flat_type: str
    block: str
    street: str
    storey_range: str
    floor_area_sqm: float
    flat_model: str | None
    lease_commencement: int
    remaining_lease: float | None
    resale_price: float
    price_per_sqm: float
    remaining_lease_months: int | None = None


@dataclass
class LLMExtractionResult:
    candidates: dict[str, Any]
    extraction_warnings: list[str]
    agent_claims: list[dict[str, Any]]
    property_category: str
    model_name: str
    raw_response: dict[str, Any] | None = None
