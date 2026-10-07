"""Structural validation tests for external-provider ingestion boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from app.adapters.base import AdapterError
from app.adapters.live.google_places import LiveGooglePlacesAdapter
from app.adapters.live.google_routes import LiveGoogleRoutesAdapter
from app.adapters.live.onemap import LiveOneMapAdapter
from app.adapters.parking.hdb_availability import AvailabilityProviderError, CarparkAvailabilityProvider
from app.adapters.provider_schemas.artifacts import (
    FairPriceModelMetadata,
    MajorRoadMappingArtifact,
    SlaMajorRoadFeatureCollection,
)
from app.adapters.provider_schemas.google_places import (
    GooglePlaceDetailsResponse,
    GooglePlacesAutocompleteResponse,
)
from app.adapters.provider_schemas.google_routes import GoogleRouteMatrixRow, GoogleRoutesResponse
from app.adapters.provider_schemas.hdb import (
    DataGovHdbCarparkResponse,
    DataGovHdbTransactionRow,
    HdbAvailabilityResponse,
    HdbCarparkSnapshotRow,
    HdbTransactionSnapshotRow,
)
from app.adapters.provider_schemas.onemap import OneMapSearchResponse
from app.adapters.provider_schemas.reference import (
    LtaBusRouteSnapshotRow,
    LtaDataMallBusRoutesResponse,
    MoeSchoolSnapshotRow,
    RailEdgeSnapshotRow,
)
from app.adapters.routing.base import RoutingProviderError


def test_onemap_schema_parses_consumed_fields_and_ignores_provider_additions() -> None:
    payload = OneMapSearchResponse.model_validate(
        {
            "results": [
                {
                    "LATITUDE": "1.3521",
                    "LONGITUDE": "103.8498",
                    "ADDRESS": "123 BISHAN STREET 12",
                    "BLK_NO": "123",
                    "ROAD_NAME": "BISHAN STREET 12",
                    "providerMayAddThis": {"without": "breaking NearHome"},
                }
            ]
        }
    )
    assert payload.results[0].latitude == 1.3521
    assert payload.results[0].block == "123"

    with pytest.raises(ValidationError):
        OneMapSearchResponse.model_validate({"results": [{"LATITUDE": "not-a-number"}]})


def test_google_places_models_validate_nested_predictions_and_details() -> None:
    autocomplete = GooglePlacesAutocompleteResponse.model_validate(
        {
            "suggestions": [
                {
                    "placePrediction": {
                        "placeId": "place-1",
                        "text": {"text": "Raffles Place, Singapore"},
                        "structuredFormat": {"mainText": {"text": "Raffles Place"}},
                    }
                }
            ]
        }
    )
    assert autocomplete.suggestions[0].place_prediction is not None

    details = GooglePlaceDetailsResponse.model_validate(
        {
            "id": "place-1",
            "formattedAddress": "Raffles Place, Singapore",
            "location": {"latitude": 1.284, "longitude": 103.851},
        }
    )
    assert details.location.longitude == 103.851

    with pytest.raises(ValidationError):
        GooglePlaceDetailsResponse.model_validate(
            {"id": "place-1", "formattedAddress": "Raffles Place", "location": "not-an-object"}
        )


def test_google_places_adapter_translates_schema_drift_to_safe_adapter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> Literal[False]:
            return False

        def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={"suggestions": [{"placePrediction": {"placeId": "missing-text"}}]},
                request=httpx.Request("POST", "https://example.test/autocomplete"),
            )

    monkeypatch.setattr("app.adapters.live.google_places.httpx.Client", lambda **_kwargs: FakeClient())
    with pytest.raises(AdapterError) as caught:
        LiveGooglePlacesAdapter().autocomplete("Raffles")
    assert caught.value.error_code == "invalid_output"


def test_google_routes_schema_validates_every_consumed_nested_field() -> None:
    payload = GoogleRoutesResponse.model_validate(
        {
            "routes": [
                {
                    "duration": "123.5s",
                    "distanceMeters": 456,
                    "legs": [
                        {
                            "steps": [
                                {
                                    "travelMode": "WALK",
                                    "staticDuration": {"seconds": "60"},
                                    "distanceMeters": 80,
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )
    assert payload.routes[0].duration_seconds == 123.5
    assert payload.routes[0].legs[0].steps[0].duration_seconds == 60

    malformed_payloads: tuple[dict[str, Any], ...] = (
        {"routes": {}},
        {"routes": [{"duration": "bad", "distanceMeters": 10}]},
        {"routes": [{"duration": "10s", "distanceMeters": -1}]},
        {"routes": [{"duration": "10s", "distanceMeters": 1, "legs": "bad"}]},
    )
    for malformed in malformed_payloads:
        with pytest.raises(ValidationError):
            GoogleRoutesResponse.model_validate(malformed)


def test_google_matrix_rows_are_validated_as_a_top_level_array() -> None:
    adapter = TypeAdapter(list[GoogleRouteMatrixRow])
    rows = adapter.validate_python(
        [{"originIndex": 0, "destinationIndex": 0, "duration": "60s", "condition": "ROUTE_EXISTS"}]
    )
    assert rows[0].duration_seconds == 60

    with pytest.raises(ValidationError):
        adapter.validate_python({"originIndex": 0})
    with pytest.raises(ValidationError):
        adapter.validate_python([{"originIndex": -1, "duration": "60s"}])


def test_legacy_matrix_adapter_rejects_malformed_success_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> Literal[False]:
            return False

        def post(self, endpoint: str, **_kwargs: object) -> httpx.Response:
            return httpx.Response(200, json={"not": "a matrix"}, request=httpx.Request("POST", endpoint))

    monkeypatch.setattr("app.adapters.live.google_routes.httpx.Client", lambda **_kwargs: FakeClient())
    with pytest.raises(RoutingProviderError) as caught:
        LiveGoogleRoutesAdapter().route_matrix(
            [(1.35, 103.85)],
            (1.30, 103.80),
            "DRIVING",
            datetime(2026, 8, 30, tzinfo=UTC),
        )
    assert caught.value.error_code == "invalid_output"


def test_hdb_availability_schema_converts_counts_and_rejects_impossible_values() -> None:
    payload = HdbAvailabilityResponse.model_validate(
        {
            "items": [
                {
                    "timestamp": "2026-08-30T12:00:00+08:00",
                    "carpark_data": [
                        {
                            "carpark_number": "ACB",
                            "carpark_info": [{"lot_type": "C", "total_lots": "100", "lots_available": "25"}],
                        }
                    ],
                }
            ]
        }
    )
    assert payload.items[0].carpark_data[0].carpark_info[0].total_lots == 100

    with pytest.raises(ValidationError):
        HdbAvailabilityResponse.model_validate(
            {
                "items": [
                    {
                        "carpark_data": [
                            {
                                "carpark_number": "ACB",
                                "carpark_info": [{"lot_type": "C", "total_lots": "10", "lots_available": "11"}],
                            }
                        ]
                    }
                ]
            }
        )


def test_hdb_availability_provider_converts_malformed_payload_to_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": []}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AvailabilityProviderError, match="temporarily unavailable"):
        CarparkAvailabilityProvider(client=client).fetch()
    client.close()


def test_hdb_snapshot_models_validate_consumed_rows() -> None:
    carpark = HdbCarparkSnapshotRow.model_validate(
        {
            "car_park_no": "ACB",
            "address": "ALBERT CENTRE",
            "latitude": 1.301,
            "longitude": 103.854,
            "carpark_decks": "1",
        }
    )
    assert carpark.carpark_no == "ACB"
    assert carpark.carpark_decks == 1

    with pytest.raises(ValidationError):
        HdbTransactionSnapshotRow.model_validate(
            {
                "transaction_id": "x",
                "transaction_month": "August 2026",
                "town": "BISHAN",
                "flat_type": "4 ROOM",
                "block": "123",
                "street": "BISHAN ST 12",
                "storey_range": "10 TO 12",
                "floor_area_sqm": 90,
                "lease_commencement": 1990,
                "resale_price": 800000,
                "price_per_sqm": 8888,
            }
        )


def test_lta_moe_and_rail_snapshot_rows_reject_invalid_structure() -> None:
    with pytest.raises(ValidationError):
        LtaBusRouteSnapshotRow.model_validate(
            {"service_no": "10", "direction": 3, "stop_sequence": 1, "bus_stop_code": "01012"}
        )
    with pytest.raises(ValidationError):
        MoeSchoolSnapshotRow.model_validate(
            {"school_name": "School", "level": "Primary", "latitude": 1.3, "longitude": 300, "address": "SG"}
        )
    with pytest.raises(ValidationError):
        RailEdgeSnapshotRow.model_validate(
            {"from_node": "NS1", "to_node": "NS2", "line": "NSL", "edge_type": "teleport", "estimated_minutes": 2}
        )


def test_maintenance_ingestion_envelopes_validate_before_snapshot_writes() -> None:
    carparks = DataGovHdbCarparkResponse.model_validate(
        {
            "result": {
                "records": [
                    {
                        "car_park_no": "ACB",
                        "address": "ALBERT CENTRE",
                        "x_coord": "30314.79",
                        "y_coord": "31490.49",
                    }
                ]
            }
        }
    )
    assert carparks.result.records[0].x_coord == 30314.79

    transaction = DataGovHdbTransactionRow.model_validate(
        {
            "month": "2026-08",
            "town": "BISHAN",
            "flat_type": "4 ROOM",
            "block": "123",
            "street_name": "BISHAN STREET 12",
            "storey_range": "10 TO 12",
            "floor_area_sqm": "90",
            "flat_model": "Model A",
            "lease_commence_date": "1990",
            "remaining_lease": "62 years 4 months",
            "resale_price": "800000",
        }
    )
    assert transaction.floor_area_sqm == 90

    with pytest.raises(ValidationError):
        DataGovHdbTransactionRow.model_validate(
            {
                "month": "2026-08",
                "town": "BISHAN",
                "flat_type": "4 ROOM",
                "block": "123",
                "street_name": "BISHAN STREET 12",
                "storey_range": "10 TO 12",
                "lease_commence_date": "1990",
                "resale_price": "800000",
            }
        )

    routes = LtaDataMallBusRoutesResponse.model_validate(
        {
            "value": [
                {
                    "ServiceNo": "10",
                    "Direction": 1,
                    "StopSequence": 1,
                    "BusStopCode": "75009",
                    "Distance": "0",
                }
            ]
        }
    )
    assert routes.value[0].bus_stop_code == "75009"

    sla = SlaMajorRoadFeatureCollection.model_validate(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"FOLDERPATH": "Layers/Major_Road", "NAME": "TEST ROAD"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[103.8, 1.3, 0], [103.81, 1.31, 0]],
                    },
                }
            ],
        }
    )
    assert sla.features[0].properties.name == "TEST ROAD"

    with pytest.raises(ValidationError):
        LtaDataMallBusRoutesResponse.model_validate(
            {
                "value": [
                    {"ServiceNo": "10", "Direction": 9, "StopSequence": 1, "BusStopCode": "75009"}
                ]
            }
        )


def test_versioned_artifact_schemas_reject_incompatible_or_incomplete_metadata() -> None:
    with pytest.raises(ValidationError):
        MajorRoadMappingArtifact.model_validate({"schema_version": 1, "metadata": {}, "major_roads": {}})

    with pytest.raises(ValidationError):
        FairPriceModelMetadata.model_validate(
            {
                "model_version": "catboost_v2_artifact",
                "transaction_snapshot_key": "hash",
                "feature_columns": ["town"],
                "numeric_medians": {"floor_area_sqm": 90},
                "residual_low": 10,
                "residual_high": -10,
                "training_rows": 100,
                "calibration_rows": 10,
                "calibration_source": "temporal_holdout",
                "supported_flat_types": ["4 ROOM"],
                "training_cutoff_month": "2026-08",
            }
        )


def test_onemap_adapter_rejects_malformed_search_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> Literal[False]:
            return False

        def get(self, endpoint: str, **_kwargs: object) -> httpx.Response:
            return httpx.Response(200, json={"results": "not-a-list"}, request=httpx.Request("GET", endpoint))

    adapter = LiveOneMapAdapter()
    monkeypatch.setattr(adapter, "_get_token", lambda: "token")
    monkeypatch.setattr("app.adapters.live.onemap.httpx.Client", lambda **_kwargs: FakeClient())
    with pytest.raises(ValueError, match="malformed search response"):
        adapter._geocode_query("Bishan", "Bishan")
