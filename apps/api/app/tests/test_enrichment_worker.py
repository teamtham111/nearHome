"""Worker state transitions are deterministic under Cloud Tasks redelivery."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.worker_main import CloudTaskPayload, run_enrichment_task


class FakeJobRepository:
    job = None
    completed = 0
    requeued = 0
    failed = 0

    def __init__(self, _db) -> None:
        pass

    def get(self, _job_id):
        return self.job

    def claim(self, _job_id):
        if self.job.status != "queued":
            return None
        self.job.status = "running"
        self.job.attempts += 1
        return self.job

    def requeue_if_stale(self, _job_id, _stale_before):
        return False

    def set_stage(self, _job_id, stage):
        self.job.progress_stage = stage

    def complete(self, _job_id, _result):
        type(self).completed += 1
        type(self).job.status = "completed"

    def requeue_retryable(self, _job_id, **_kwargs):
        type(self).requeued += 1
        type(self).job.status = "queued"

    def fail_permanently(self, _job_id, **_kwargs):
        type(self).failed += 1
        type(self).job.status = "failed"


def _job(status: str = "queued"):
    return SimpleNamespace(
        id=uuid4(),
        session_id=uuid4(),
        status=status,
        attempts=0,
        progress_stage="queued",
    )


def _patch_worker(monkeypatch, result=None, error: Exception | None = None):
    from app import worker_main

    FakeJobRepository.completed = 0
    FakeJobRepository.requeued = 0
    FakeJobRepository.failed = 0
    monkeypatch.setattr(worker_main, "EnrichmentJobRepository", FakeJobRepository)

    class FakeService:
        def __init__(self, _db) -> None:
            pass

        def run_session_enrichment(self, _session_id, *, simulate_delay, progress_callback, job_id):
            assert simulate_delay is False
            assert job_id == FakeJobRepository.job.id
            progress_callback("calculating_fair_price")
            if error:
                raise error
            return result or {"listings": [{"listing_id": "one"}], "errors": []}

    monkeypatch.setattr(worker_main, "EnrichmentService", FakeService)


def test_worker_completes_once_and_duplicate_delivery_is_safe(monkeypatch) -> None:
    _patch_worker(monkeypatch)
    FakeJobRepository.job = _job()
    payload = CloudTaskPayload(job_id=FakeJobRepository.job.id)

    first = run_enrichment_task(payload, None, object())
    second = run_enrichment_task(payload, None, object())

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert FakeJobRepository.completed == 1
    assert FakeJobRepository.job.progress_stage == "calculating_fair_price"


def test_worker_leaves_a_nonstale_running_duplicate_alone(monkeypatch) -> None:
    _patch_worker(monkeypatch)
    FakeJobRepository.job = _job("running")

    response = run_enrichment_task(CloudTaskPayload(job_id=FakeJobRepository.job.id), None, object())

    assert response["status"] == "already_running"
    assert FakeJobRepository.completed == 0


def test_worker_requeues_retryable_failure_for_cloud_tasks(monkeypatch) -> None:
    _patch_worker(monkeypatch, error=httpx.TimeoutException("provider timed out"))
    FakeJobRepository.job = _job()

    with pytest.raises(HTTPException) as exc_info:
        run_enrichment_task(CloudTaskPayload(job_id=FakeJobRepository.job.id), None, object())

    assert exc_info.value.status_code == 503
    assert FakeJobRepository.requeued == 1
    assert FakeJobRepository.job.status == "queued"


def test_worker_marks_permanent_failure_without_retry(monkeypatch) -> None:
    _patch_worker(monkeypatch, error=ValueError("session no longer exists"))
    FakeJobRepository.job = _job()

    response = run_enrichment_task(CloudTaskPayload(job_id=FakeJobRepository.job.id), None, object())

    assert response["status"] == "failed"
    assert FakeJobRepository.failed == 1
