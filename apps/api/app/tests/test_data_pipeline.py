from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_pipeline.data_gov_client import DataGovClient, _extract_zip_member  # noqa: E402
from data_pipeline.ingest_moe_schools import transform  # noqa: E402
from data_pipeline.run_pipeline import selected_stages  # noqa: E402
from data_pipeline.source_catalog import SOURCES, by_id  # noqa: E402


class _SequenceClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, **_: object) -> httpx.Response:
        self.urls.append(url)
        return self.responses.pop(0)


def _response(payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("GET", "https://example.test"))


def test_source_catalog_is_unique_and_covers_every_evidence_family() -> None:
    source_ids = [source.id for source in SOURCES]
    assert len(source_ids) == len(set(source_ids))
    assert {
        "buyer-input",
        "listing-pages",
        "groq-smart-paste",
        "onemap-geocoding",
        "google-places",
        "google-routes",
        "hdb-resale-transactions",
        "hdb-carpark-information",
        "hdb-carpark-availability",
        "moe-school-directory",
        "lta-bus-stops",
        "lta-bus-routes",
        "lta-bus-services",
        "rail-structure",
        "sla-national-map-line",
        "openstreetmap-drive-graph",
        "google-roads-qa",
        "catboost-fair-price-model",
    } <= set(source_ids)
    assert by_id("moe-school-directory").owner.startswith("Ministry of Education")


def test_data_gov_download_poll_retries_until_signed_url() -> None:
    client = _SequenceClient(
        [
            _response({"code": 0, "data": {}}),
            _response({"code": 0, "data": {"url": "https://download.example.test/file.csv"}}),
        ]
    )
    result = DataGovClient(client=client, sleeper=lambda _: None).prepare_download(
        "d_123", initiate=False, max_polls=2
    )
    assert result == "https://download.example.test/file.csv"
    assert len(client.urls) == 2


def test_zip_download_selects_requested_dataset_type() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "ignore")
        archive.writestr("nested/schools.csv", "school_name,address\nA,One Road\n")
    assert _extract_zip_member(buffer.getvalue(), ".csv").startswith(b"school_name,address")


def test_moe_transform_reuses_unchanged_coordinates_and_geocodes_changed_rows() -> None:
    calls: list[str] = []

    def geocode(query: str) -> tuple[float, float]:
        calls.append(query)
        return 1.31, 103.81

    rows = transform(
        [
            {
                "school_name": "Cached Primary",
                "address": "1 OLD ROAD",
                "postal_code": "123456",
                "mainlevel_code": "PRIMARY",
            },
            {
                "school_name": "New Secondary",
                "address": "2 NEW ROAD",
                "postal_code": "654321",
                "mainlevel_code": "SECONDARY",
            },
        ],
        geocode,
        cached={"cached primary": (1.30, 103.80, "1 old road")},
    )
    assert rows[0]["latitude"] == 1.30
    assert rows[1]["latitude"] == 1.31
    assert calls == ["654321"]


def test_all_profile_orders_dependencies_before_consumers() -> None:
    stages = selected_stages("all")
    indexes = {stage.id: index for index, stage in enumerate(stages)}
    for stage in stages:
        for dependency in stage.dependencies:
            if dependency in indexes:
                assert indexes[dependency] < indexes[stage.id]
