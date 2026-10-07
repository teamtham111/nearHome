"""Schemas for HDB/data.gov.sg availability and normalized snapshots."""

from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, Field, field_validator, model_validator

from app.adapters.provider_schemas.common import ProviderPayload, empty_to_none


class HdbAvailabilityInfo(ProviderPayload):
    total_lots: int | None = Field(default=None, ge=0)
    lots_available: int | None = Field(default=None, ge=0)
    lot_type: str = Field(min_length=1)

    @field_validator("total_lots", "lots_available", mode="before")
    @classmethod
    def normalize_empty_counts(cls, value: object) -> object:
        return empty_to_none(value)

    @model_validator(mode="after")
    def validate_available_lots(self) -> HdbAvailabilityInfo:
        if self.total_lots is not None and self.lots_available is not None and self.lots_available > self.total_lots:
            raise ValueError("lots_available cannot exceed total_lots")
        return self


class HdbAvailabilityCarpark(ProviderPayload):
    carpark_number: str = Field(min_length=1)
    update_datetime: str | None = None
    carpark_info: list[HdbAvailabilityInfo] = Field(default_factory=list)


class HdbAvailabilitySnapshot(ProviderPayload):
    timestamp: str | None = None
    carpark_data: list[HdbAvailabilityCarpark] = Field(default_factory=list)


class HdbAvailabilityResponse(ProviderPayload):
    items: list[HdbAvailabilitySnapshot]


class DataGovHdbCarparkRecord(ProviderPayload):
    carpark_no: str = Field(alias="car_park_no", min_length=1)
    address: str = Field(min_length=1)
    x_coord: float = Field(allow_inf_nan=False)
    y_coord: float = Field(allow_inf_nan=False)
    carpark_type: str | None = Field(default=None, alias="car_park_type")
    parking_system_type: str | None = Field(default=None, alias="type_of_parking_system")
    short_term_parking: str | None = None
    free_parking: str | None = None
    night_parking: str | None = None
    carpark_decks: int | None = Field(default=None, alias="car_park_decks", ge=0)
    gantry_height_m: float | None = Field(default=None, alias="gantry_height", ge=0, allow_inf_nan=False)
    basement_indicator: str | None = Field(default=None, alias="car_park_basement")

    @field_validator("carpark_decks", "gantry_height_m", mode="before")
    @classmethod
    def normalize_empty_provider_numbers(cls, value: object) -> object:
        return empty_to_none(value)


class DataGovHdbCarparkResult(ProviderPayload):
    records: list[DataGovHdbCarparkRecord]


class DataGovHdbCarparkResponse(ProviderPayload):
    result: DataGovHdbCarparkResult


class DataGovHdbTransactionRow(ProviderPayload):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    town: str = Field(min_length=1)
    flat_type: str = Field(min_length=1)
    block: str = Field(min_length=1)
    street: str = Field(min_length=1, validation_alias=AliasChoices("street_name", "street"))
    storey_range: str = Field(min_length=1)
    floor_area_sqm: float = Field(
        gt=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices("floor_area_sqm", "floor_area"),
    )
    flat_model: str | None = None
    lease_commencement: int = Field(
        ge=1900,
        validation_alias=AliasChoices("lease_commence_date", "lease_commencement"),
    )
    remaining_lease: str | float | int | None = None
    resale_price: float = Field(gt=0, allow_inf_nan=False)


class HdbCarparkSnapshotRow(ProviderPayload):
    carpark_no: str = Field(
        min_length=1,
        validation_alias=AliasChoices("carpark_no", "car_park_no"),
    )
    address: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    carpark_type: str | None = None
    source_carpark_type: str | None = None
    parking_system_type: str | None = None
    short_term_parking: str | None = None
    free_parking: str | None = None
    night_parking: str | None = None
    carpark_decks: int | None = Field(default=None, ge=0)
    gantry_height_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    basement_indicator: str | None = None
    source: str | None = None
    source_updated_at: datetime | None = None

    @field_validator("carpark_decks", "gantry_height_m", "source_updated_at", mode="before")
    @classmethod
    def normalize_empty_optional_values(cls, value: object) -> object:
        return empty_to_none(value)


class HdbTransactionSnapshotRow(ProviderPayload):
    transaction_id: str = Field(min_length=1)
    transaction_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    town: str = Field(min_length=1)
    flat_type: str = Field(min_length=1)
    block: str = Field(min_length=1)
    street: str = Field(min_length=1)
    storey_range: str = Field(min_length=1)
    floor_area_sqm: float = Field(gt=0, allow_inf_nan=False)
    flat_model: str | None = None
    lease_commencement: int
    remaining_lease: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    resale_price: float = Field(gt=0, allow_inf_nan=False)
    price_per_sqm: float = Field(gt=0, allow_inf_nan=False)
    remaining_lease_months: int | None = Field(default=None, ge=0)
