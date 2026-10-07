"""Schemas for versioned road-network and fair-price model artifacts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, model_validator

from app.adapters.provider_schemas.common import ProviderPayload

EdgeIdentifier = tuple[str, str, str]
# GeoJSON positions contain at least longitude/latitude and may include an
# elevation ordinate. NearHome consumes only the first two values.
GeoPosition = Annotated[list[FiniteFloat], Field(min_length=2)]


class SlaLineStringGeometry(ProviderPayload):
    type: Literal["LineString"]
    coordinates: list[GeoPosition] = Field(min_length=2)


class SlaMultiLineStringGeometry(ProviderPayload):
    type: Literal["MultiLineString"]
    coordinates: list[list[GeoPosition]] = Field(min_length=1)


SlaMajorRoadGeometry = Annotated[
    SlaLineStringGeometry | SlaMultiLineStringGeometry,
    Field(discriminator="type"),
]


class SlaMajorRoadProperties(ProviderPayload):
    folder_path: str = Field(alias="FOLDERPATH")
    name: str | None = Field(default=None, alias="NAME")


class SlaMajorRoadFeature(ProviderPayload):
    type: Literal["Feature"] = "Feature"
    properties: SlaMajorRoadProperties
    geometry: SlaMajorRoadGeometry


class SlaMajorRoadFeatureCollection(ProviderPayload):
    type: Literal["FeatureCollection"]
    source: str | None = None
    filtered_folderpath: str | None = None
    retrieved_at: str | None = None
    features: list[SlaMajorRoadFeature]


class MajorRoadEntryNodeArtifact(ProviderPayload):
    node_id: str = Field(min_length=1)
    latitude: FiniteFloat
    longitude: FiniteFloat
    matched_osm_edge_ids: list[EdgeIdentifier] = Field(default_factory=list)
    candidate_id: str = Field(min_length=1)
    target_latitude: FiniteFloat
    target_longitude: FiniteFloat
    approach_osm_edge_ids: list[EdgeIdentifier] = Field(default_factory=list)
    matching_confidence: str = "TOPOLOGY_VALIDATED"


class MajorRoadArtifactRecord(ProviderPayload):
    name: str = Field(min_length=1)
    matched_osm_edge_ids: list[EdgeIdentifier] = Field(default_factory=list)
    name_supported_edge_count: int = Field(default=0, ge=0)
    entry_nodes: list[MajorRoadEntryNodeArtifact]


class MajorRoadArtifactMetadata(ProviderPayload):
    graph_sha256: str = Field(min_length=1)
    sla_sha256: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    matching_algorithm_version: str = Field(min_length=1)
    strtree_search_radius_metres: FiniteFloat = Field(gt=0)
    catalogue_version: str = Field(min_length=1)


class MajorRoadMappingArtifact(ProviderPayload):
    schema_version: Literal[2]
    metadata: MajorRoadArtifactMetadata
    major_roads: dict[str, MajorRoadArtifactRecord]


class FairPriceModelMetadata(ProviderPayload):
    model_version: str = Field(min_length=1)
    transaction_snapshot_key: str = Field(min_length=1)
    feature_columns: list[str] = Field(min_length=1)
    numeric_medians: dict[str, FiniteFloat]
    residual_low: FiniteFloat
    residual_high: FiniteFloat
    training_rows: int = Field(gt=0)
    calibration_rows: int = Field(ge=0)
    calibration_source: str = Field(min_length=1)
    supported_flat_types: list[str] = Field(min_length=1)
    training_cutoff_month: str = Field(pattern=r"^\d{4}-\d{2}$")

    @model_validator(mode="after")
    def validate_residual_interval(self) -> FairPriceModelMetadata:
        if self.residual_low > self.residual_high:
            raise ValueError("residual_low cannot exceed residual_high")
        if any(not flat_type.strip() for flat_type in self.supported_flat_types):
            raise ValueError("supported_flat_types cannot contain empty values")
        return self
