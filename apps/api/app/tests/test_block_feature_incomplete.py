"""Incomplete block results: kept as evidence, retried by the job, never reused live."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.jobs import location_benchmarks as job
from app.services.block_features import (
    BlockFeatureService,
    calculation_version,
    compress_payload,
    incomplete_routing_state,
    source_fingerprint,
)


def _payload(*, failed: int = 0, retryable: int = 0, detours: int = 0, status: str = "partially_assessed") -> dict:
    return {
        "components": [
            {
                "name": "access",
                "status": status,
                "score": 40.0,
                "value": {
                    "routing_failed_requests": failed,
                    "retryable_failed_requests": retryable,
                    "walking_detours": [{}] * detours,
                },
            }
        ]
    }


COMPLETE = _payload(status="calculated")
NO_ACCESS = {**_payload(status="calculated"), "note": "no practical access found"}
TRANSIENT = _payload(failed=2, retryable=2)
DETOUR_ONLY = _payload(detours=1)
NO_ROUTE_ONLY = _payload(failed=1, retryable=0)


class FakeRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], Any] = {}

    def get_feature(self, block_key: str, feature_set: str, version: str) -> Any:
        return self.rows.get((block_key, feature_set, version))

    def save_feature(self, **values: Any) -> Any:
        row = SimpleNamespace(**values, computed_at=datetime.now(UTC))
        self.rows[(values["block_key"], values["feature_set"], values["calculation_version"])] = row
        return row

    # job-facing
    def residential_blocks(self) -> list[Any]:
        return [SimpleNamespace(block_key=k, latitude=1.3, longitude=103.8, town_code="X") for k in ("A", "B", "C")]


def _store(repo: FakeRepo, key: str, payload: dict, *, status: str) -> None:
    repo.rows[(key, "public_transport", calculation_version("public_transport"))] = SimpleNamespace(
        block_key=key,
        status=status,
        source_fingerprint=source_fingerprint("public_transport"),
        computed_at=datetime.now(UTC),
        origin_latitude=1.3,
        origin_longitude=103.8,
        payload_gz=compress_payload(payload),
    )


def _measure(service: BlockFeatureService, payload: dict, key: str = "A", **kwargs: Any):
    return service.get_or_compute(key, "public_transport", (1.3, 103.8), "t", lambda *_: payload, reuse=False, **kwargs)


def test_incomplete_state_distinguishes_transient_final_and_complete() -> None:
    assert incomplete_routing_state("public_transport", COMPLETE) is None
    assert incomplete_routing_state("public_transport", TRANSIENT)["retryable"] is True
    assert incomplete_routing_state("public_transport", DETOUR_ONLY)["retryable"] is False
    assert incomplete_routing_state("public_transport", NO_ROUTE_ONLY)["retryable"] is False
    assert incomplete_routing_state("driving_baseline", TRANSIENT) is None


def test_backfill_stores_incomplete_result_as_evidence_with_attempt_count() -> None:
    repo = FakeRepo()
    service = BlockFeatureService(repo)  # type: ignore[arg-type]
    outcome = _measure(service, TRANSIENT, store_incomplete=True)
    row = repo.get_feature("A", "public_transport", calculation_version("public_transport"))
    assert row.status == "incomplete"
    assert outcome.incomplete["attempts"] == 1
    _measure(service, TRANSIENT, store_incomplete=True)
    assert service.cached("A", "public_transport", include_incomplete=True).incomplete["attempts"] == 2


def test_job_reselects_a_block_that_is_incomplete_because_of_failed_requests() -> None:
    repo = FakeRepo()
    service = BlockFeatureService(repo)  # type: ignore[arg-type]
    _measure(service, TRANSIENT, store_incomplete=True)
    assert service.needs_measurement("A", "public_transport") is True


def test_job_stops_retrying_after_the_attempt_cap() -> None:
    repo = FakeRepo()
    service = BlockFeatureService(repo)  # type: ignore[arg-type]
    for _ in range(3):
        _measure(service, TRANSIENT, store_incomplete=True)
    assert service.needs_measurement("A", "public_transport", max_attempts=3) is False
    assert service.needs_measurement("A", "public_transport", max_attempts=4) is True


@pytest.mark.parametrize("payload", [COMPLETE, NO_ACCESS, DETOUR_ONLY, NO_ROUTE_ONLY])
def test_job_does_not_reselect_complete_or_deterministic_results(payload: dict) -> None:
    repo = FakeRepo()
    service = BlockFeatureService(repo)  # type: ignore[arg-type]
    _measure(service, payload, store_incomplete=True)
    assert service.needs_measurement("A", "public_transport") is False


def test_job_selects_a_block_with_no_stored_result() -> None:
    assert BlockFeatureService(FakeRepo()).needs_measurement("Z", "public_transport") is True  # type: ignore[arg-type]


def test_a_complete_retry_replaces_the_incomplete_row() -> None:
    repo = FakeRepo()
    service = BlockFeatureService(repo)  # type: ignore[arg-type]
    _measure(service, TRANSIENT, store_incomplete=True)
    _measure(service, COMPLETE, store_incomplete=True)
    assert service.cached("A", "public_transport") is not None
    assert service.needs_measurement("A", "public_transport") is False


@pytest.mark.parametrize("payload", [TRANSIENT, DETOUR_ONLY])
def test_live_path_ignores_a_stored_incomplete_row_and_computes_live(payload: dict) -> None:
    repo = FakeRepo()
    _store(repo, "A", {**payload, "marker": "stored"}, status="incomplete")
    service = BlockFeatureService(repo)  # type: ignore[arg-type]
    assert service.cached("A", "public_transport") is None
    outcome = service.get_or_compute(
        "A", "public_transport", (1.3, 103.8), "t", lambda *_: {**COMPLETE, "marker": "live"}
    )
    assert outcome.reused is False and outcome.payload["marker"] == "live"


def test_live_path_never_stores_an_incomplete_result() -> None:
    repo = FakeRepo()
    outcome = BlockFeatureService(repo).get_or_compute(  # type: ignore[arg-type]
        "A", "public_transport", (1.3, 103.8), "t", lambda *_: TRANSIENT
    )
    assert outcome.reused is False and repo.rows == {}


def test_live_path_still_reuses_a_complete_result() -> None:
    repo = FakeRepo()
    _store(repo, "A", {**COMPLETE, "marker": "stored"}, status="available")
    outcome = BlockFeatureService(repo).get_or_compute(  # type: ignore[arg-type]
        "A", "public_transport", (1.3, 103.8), "t", lambda *_: {"marker": "live"}
    )
    assert outcome.reused is True and outcome.payload["marker"] == "stored"


def test_measure_command_retries_only_the_incomplete_block(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo()
    _store(repo, "A", COMPLETE, status="available")
    _store(
        repo,
        "B",
        {
            **TRANSIENT,
            "benchmark_incomplete": {**incomplete_routing_state("public_transport", TRANSIENT), "attempts": 1},
        },
        status="incomplete",
    )  # type: ignore[dict-item]
    computed: list[str] = []

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def commit(self) -> None: ...
        def rollback(self) -> None: ...

    def fake_compute(feature_set: str, block: Any, *_: Any):
        def run(*_coords: float) -> dict:
            computed.append(block.block_key)
            return COMPLETE

        return run

    monkeypatch.setattr(job, "SessionLocal", Session)
    monkeypatch.setattr(job, "BlockRepository", lambda _db: repo)
    monkeypatch.setattr(job, "compute_function", fake_compute)
    monkeypatch.setattr(
        job, "free_routing_provider", lambda: SimpleNamespace(provider_name="MOCK_ROUTING", request_count=0)
    )
    args = argparse.Namespace(
        features="public_transport", town=None, limit=0, max_requests=0, force=False, max_incomplete_attempts=0
    )
    report = job.cmd_measure(args)
    assert sorted(computed) == ["B", "C"]  # A is complete; B was incomplete; C never measured
    assert report["public_transport"]["blocks_selected"] == 2
