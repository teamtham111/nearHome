"""API integration tests — require PostgreSQL (skipped if unavailable)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
    from app.core.config import settings

    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["git_sha"] == settings.release_sha


def test_ready_exposes_release_sha(client: TestClient):
    from app.core.config import settings

    resp = client.get("/api/v1/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"ready", "degraded"}
    assert resp.json()["git_sha"] == settings.release_sha


def test_health_and_ready_expose_the_same_configured_release_sha(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import routes

    release_sha = "b" * 40
    monkeypatch.setattr(routes.settings, "git_sha", release_sha)

    health = client.get("/api/v1/health")
    ready = client.get("/api/v1/ready")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.json()["git_sha"] == release_sha
    assert ready.json()["git_sha"] == release_sha


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
    assert all(score["overall_fit_score"] == score["total_score"] for score in comparison["preference_scores"])
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
    assert client.delete(f"/api/v1/sessions/{session_id}/listings/{deleted['listing_id']}").status_code == 204
    assert client.get(f"/api/v1/listing-inputs/{deleted['listing_input_id']}").status_code == 404

    from uuid import UUID

    from app.db.session import SessionLocal
    from app.repositories.enrichment_repository import EnrichmentRepository

    with SessionLocal() as db:
        enrichment_repo = EnrichmentRepository(db)
        deleted_id = UUID(deleted["listing_id"])
        assert enrichment_repo.upsert_run(deleted_id, "FAIR_PRICE", "RUNNING") is None
        enrichment_repo.save_enriched_field(deleted_id, "fair_price", {"central_estimate": 1}, "AVAILABLE", "TEST")
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
    assert deleted["listing_id"] not in {metric["listing_id"] for metric in comparison.json()["immediate_metrics"]}

    assert client.delete(f"/api/v1/sessions/{session_id}/listings/{created[0]['listing_id']}").status_code == 204
    one_left = client.get(f"/api/v1/sessions/{session_id}/comparison")
    assert one_left.status_code == 200
    assert one_left.json()["listing_count"] == 1
    assert one_left.json()["can_compare"] is False

    assert client.delete(f"/api/v1/sessions/{session_id}/listings/{created[2]['listing_id']}").status_code == 204
    empty = client.get(f"/api/v1/sessions/{session_id}/comparison")
    assert empty.status_code == 200
    assert empty.json()["listing_count"] == 0
    assert empty.json()["can_compare"] is False
    assert client.delete(f"/api/v1/sessions/{session_id}/listings/{created[2]['listing_id']}").status_code == 404


def test_smart_paste_flow(client: TestClient, monkeypatch):
    from app.adapters.mock.groq import MockGroqAdapter
    from app.services.smart_paste import service as smart_paste_service

    monkeypatch.setattr(smart_paste_service, "get_llm_adapter", lambda: MockGroqAdapter())
    session = client.post("/api/v1/sessions").json()
    session_id = session["session_id"]

    paste_text = (
        """
    4 ROOM FLAT FOR SALE
    Blk 123 Bishan St 12
    Asking Price: $650,000
    Floor Area: 91 sqm
    Flat Type: 4 ROOM
    """
        * 2
    )

    resp = client.post(
        f"/api/v1/sessions/{session_id}/smart-paste",
        json={"text": paste_text},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "listing_input_id" in data
    assert client.delete(f"/api/v1/sessions/{session_id}/listing-inputs/{data['listing_input_id']}").status_code == 204
    assert client.get(f"/api/v1/listing-inputs/{data['listing_input_id']}").status_code == 404


def test_local_enrichment_job(client: TestClient):
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
    assert enrich.status_code == 202
    job = enrich.json()
    assert job["job_id"]
    assert job["status_url"].endswith(f"session_id={session_id}")

    job_status = client.get(f"/api/v1/jobs/{job['job_id']}?session_id={session_id}")
    assert job_status.status_code == 200
    assert job_status.json()["status"] in {"completed", "failed"}

    comparison = client.get(f"/api/v1/sessions/{session_id}/comparison").json()
    if job_status.json()["status"] == "completed":
        assert comparison["fair_price_status"] in ("AVAILABLE", "INSUFFICIENT_EVIDENCE")


def test_inline_enrichment_runs_off_the_event_loop(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """A long inline run must not block the event loop that serves status polling."""
    import asyncio

    from app.api import routes
    from app.services.enrichment_service import EnrichmentService

    monkeypatch.setattr(routes.settings, "job_execution_mode", "inline")
    observed: dict[str, bool] = {}

    def fake_run(self, session_id, **kwargs):
        try:
            asyncio.get_running_loop()
            observed["on_event_loop"] = True
        except RuntimeError:
            observed["on_event_loop"] = False
        return {"listings": [], "errors": []}

    monkeypatch.setattr(EnrichmentService, "run_session_enrichment", fake_run)
    session_id = client.post("/api/v1/sessions").json()["session_id"]

    resp = client.post(f"/api/v1/sessions/{session_id}/enrichment/start")

    assert resp.status_code == 202
    assert resp.json()["status"] == "completed"
    assert observed == {"on_event_loop": False}


def test_polling_expires_a_stale_active_enrichment_job(client: TestClient):
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.repositories.enrichment_job_repository import EnrichmentJobRepository

    session_id = client.post("/api/v1/sessions").json()["session_id"]
    with SessionLocal() as db:
        job, created = EnrichmentJobRepository(db).create_or_get_active(session_id)
        assert created is True
        job_id = job.id
        job.updated_at = datetime.now(UTC) - timedelta(seconds=settings.enrichment_job_stale_seconds + 1)
        db.commit()

    response = client.get(f"/api/v1/jobs/{job_id}?session_id={session_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["progress_stage"] == "failed"
    assert response.json()["error_code"] == "enrichment_job_timed_out"


def test_session_feedback_upserts_one_rating_per_session(client: TestClient):
    session_id = client.post("/api/v1/sessions").json()["session_id"]

    first = client.put(f"/api/v1/sessions/{session_id}/feedback", json={"rating": 4})
    assert first.status_code == 200
    assert first.json() == {"rating": 4, "comment": None}

    updated = client.put(
        f"/api/v1/sessions/{session_id}/feedback",
        json={"rating": 5, "comment": "  Clear comparison  "},
    )
    assert updated.json() == {"rating": 5, "comment": "Clear comparison"}

    rating_only = client.put(f"/api/v1/sessions/{session_id}/feedback", json={"rating": 3})
    assert rating_only.json() == {"rating": 3, "comment": "Clear comparison"}


def test_session_feedback_rejects_invalid_ratings_and_unknown_sessions(client: TestClient):
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    for rating in (0, 6):
        response = client.put(f"/api/v1/sessions/{session_id}/feedback", json={"rating": rating})
        assert response.status_code == 422

    missing = client.put(f"/api/v1/sessions/{uuid4()}/feedback", json={"rating": 5})
    assert missing.status_code == 404


def _manual_listing(address: str) -> dict:
    return {"address": address, "asking_price": 400000, "floor_area_sqm": 70, "flat_type": "3 ROOM"}


def test_sixth_listing_is_rejected_with_conflict_not_server_error(client: TestClient):
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    for block in range(1, 6):
        resp = client.post(
            f"/api/v1/sessions/{session_id}/listings/manual", json=_manual_listing(f"Blk {block} Limit St")
        )
        assert resp.status_code == 201

    resp = client.post(f"/api/v1/sessions/{session_id}/listings/manual", json=_manual_listing("Blk 6 Limit St"))

    assert resp.status_code == 409
    assert "up to five flats" in resp.json()["detail"]
    assert client.get(f"/api/v1/sessions/{session_id}").json()["listing_count"] == 5


def test_overlong_address_is_a_validation_error(client: TestClient):
    session_id = client.post("/api/v1/sessions").json()["session_id"]

    resp = client.post(f"/api/v1/sessions/{session_id}/listings/manual", json=_manual_listing("A" * 256))

    assert resp.status_code == 422
