"""API integration tests — require PostgreSQL (skipped if unavailable)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL required for integration tests",
)


@pytest.fixture(scope="module")
def client():
    from app.main import app

    return TestClient(app)


def test_health(client: TestClient):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_manual_listing_comparison_flow(client: TestClient):
    session = client.post("/api/v1/sessions").json()
    session_id = session["session_id"]

    profile = client.put(
        f"/api/v1/sessions/{session_id}/buyer-profile",
        json={
            "max_budget": 700000,
            "priorities": [{"priority_type": "AFFORDABILITY"}],
            "main_transport_mode": "MAINLY_PUBLIC_TRANSPORT",
            "hard_requirements": [],
            "important_locations": [
                {
                    "label": "Work",
                    "place_id": "work-place",
                    "formatted_address": "1 Raffles Place, Singapore",
                    "latitude": 1.2847,
                    "longitude": 103.8511,
                    "usual_day_type": "WEEKDAY",
                    "departure_time_local": "08:30:00",
                    "transport_mode": "PUBLIC_TRANSPORT",
                }
            ],
            "schools_matter": True,
            "named_schools": ["Raffles Institution", "Nanyang Primary School"],
        },
    )
    assert profile.status_code == 200
    saved_session = client.get(f"/api/v1/sessions/{session_id}")
    assert saved_session.status_code == 200
    assert saved_session.json()["buyer_profile"]["named_schools"] == [
        "Raffles Institution",
        "Nanyang Primary School",
    ]
    saved_location = saved_session.json()["buyer_profile"]["important_locations"][0]
    assert saved_location["formatted_address"] == "1 Raffles Place, Singapore"
    assert saved_location["latitude"] == 1.2847
    assert saved_location["departure_time_local"] == "08:30:00"

    for price, address in [(650000, "123 Bishan St 12"), (680000, "125 Bishan St 12")]:
        resp = client.post(
            f"/api/v1/sessions/{session_id}/listings/manual",
            json={
                "asking_price": price,
                "floor_area_sqm": 91,
                "address": address,
                "flat_type": "4 ROOM",
                "remaining_lease_years": 65,
            },
        )
        assert resp.status_code == 201

    comparison = client.get(f"/api/v1/sessions/{session_id}/comparison").json()
    assert comparison["can_compare"] is True
    assert comparison["listing_count"] == 2
    assert len(comparison["immediate_metrics"]) > 0
    assert len(comparison["preference_scores"]) == 2
    assert all(1 < score["overall_fit_score"] < 100 for score in comparison["preference_scores"])
    assert all(
        score["overall_fit_score"] == score["total_score"]
        for score in comparison["preference_scores"]
    )
    assert {score["rank"] for score in comparison["preference_scores"]} == {1, 2}

    duplicate = client.post(
        f"/api/v1/sessions/{session_id}/listings/manual",
        json={
            "asking_price": 650000,
            "floor_area_sqm": 91,
            "address": "123 Bishan St 12",
            "flat_type": "4 ROOM",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "This address and asking price are already in your shortlist."


def test_delete_listing_removes_only_target_and_allows_small_shortlists(client: TestClient):
    session = client.post("/api/v1/sessions").json()
    session_id = session["session_id"]
    created = []
    for index, address in enumerate(("123 Bishan St 12", "201 Tampines St 21", "217 Bishan St 23")):
        response = client.post(
            f"/api/v1/sessions/{session_id}/listings/manual",
            json={
                "asking_price": 600000 + index * 10000,
                "floor_area_sqm": 91,
                "address": address,
                "flat_type": "4 ROOM",
                "remaining_lease_years": 65,
            },
        )
        assert response.status_code == 201
        created.append(response.json())

    deleted = created[1]
    assert client.delete(
        f"/api/v1/sessions/{session_id}/listings/{deleted['listing_id']}"
    ).status_code == 204
    assert client.get(f"/api/v1/listing-inputs/{deleted['listing_input_id']}").status_code == 404

    from uuid import UUID

    from app.db.session import SessionLocal
    from app.repositories.enrichment_repository import EnrichmentRepository

    with SessionLocal() as db:
        enrichment_repo = EnrichmentRepository(db)
        deleted_id = UUID(deleted["listing_id"])
        assert enrichment_repo.upsert_run(deleted_id, "FAIR_PRICE", "RUNNING") is None
        enrichment_repo.save_enriched_field(
            deleted_id, "fair_price", {"central_estimate": 1}, "AVAILABLE", "TEST"
        )
        assert enrichment_repo.get_enriched_fields(deleted_id) == []

    remaining = client.get(f"/api/v1/sessions/{session_id}").json()
    assert remaining["listing_count"] == 2
    assert {listing["listing_id"] for listing in remaining["listings"]} == {
        created[0]["listing_id"],
        created[2]["listing_id"],
    }
    comparison = client.get(f"/api/v1/sessions/{session_id}/comparison")
    assert comparison.status_code == 200
    assert comparison.json()["listing_count"] == 2
    assert deleted["listing_id"] not in {
        metric["listing_id"] for metric in comparison.json()["immediate_metrics"]
    }

    assert client.delete(
        f"/api/v1/sessions/{session_id}/listings/{created[0]['listing_id']}"
    ).status_code == 204
    one_left = client.get(f"/api/v1/sessions/{session_id}/comparison")
    assert one_left.status_code == 200
    assert one_left.json()["listing_count"] == 1
    assert one_left.json()["can_compare"] is False

    assert client.delete(
        f"/api/v1/sessions/{session_id}/listings/{created[2]['listing_id']}"
    ).status_code == 204
    empty = client.get(f"/api/v1/sessions/{session_id}/comparison")
    assert empty.status_code == 200
    assert empty.json()["listing_count"] == 0
    assert empty.json()["can_compare"] is False
    assert client.delete(
        f"/api/v1/sessions/{session_id}/listings/{created[2]['listing_id']}"
    ).status_code == 404


def test_smart_paste_flow(client: TestClient, monkeypatch):
    from app.adapters.mock.groq import MockGroqAdapter
    from app.services.smart_paste import service as smart_paste_service

    monkeypatch.setattr(smart_paste_service, "get_llm_adapter", lambda: MockGroqAdapter())
    session = client.post("/api/v1/sessions").json()
    session_id = session["session_id"]

    paste_text = """
    4 ROOM FLAT FOR SALE
    Blk 123 Bishan St 12
    Asking Price: $650,000
    Floor Area: 91 sqm
    Flat Type: 4 ROOM
    """ * 2

    resp = client.post(
        f"/api/v1/sessions/{session_id}/smart-paste",
        json={"text": paste_text},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "listing_input_id" in data
    assert client.delete(
        f"/api/v1/sessions/{session_id}/listing-inputs/{data['listing_input_id']}"
    ).status_code == 204
    assert client.get(f"/api/v1/listing-inputs/{data['listing_input_id']}").status_code == 404


def test_enrichment_inline(client: TestClient):
    session = client.post("/api/v1/sessions").json()
    session_id = session["session_id"]

    client.put(
        f"/api/v1/sessions/{session_id}/buyer-profile",
        json={
            "max_budget": 700000,
            "priorities": [{"priority_type": "AFFORDABILITY"}],
            "main_transport_mode": "MAINLY_PUBLIC_TRANSPORT",
            "hard_requirements": [],
            "important_locations": [],
        },
    )

    for price in [640000, 670000]:
        client.post(
            f"/api/v1/sessions/{session_id}/listings/manual",
            json={
                "asking_price": price,
                "floor_area_sqm": 91,
                "address": f"Blk {price} Bishan St 12",
                "flat_type": "4 ROOM",
            },
        )

    enrich = client.post(f"/api/v1/sessions/{session_id}/enrichment/start")
    assert enrich.status_code == 200
    assert enrich.json()["mode"] in ("inline", "queued")

    comparison = client.get(f"/api/v1/sessions/{session_id}/comparison").json()
    if enrich.json()["mode"] == "inline":
        assert comparison["fair_price_status"] in ("AVAILABLE", "INSUFFICIENT_EVIDENCE")
