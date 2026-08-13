"""Execution mode behavior independent of Redis and ARQ infrastructure."""

import asyncio
from uuid import uuid4

from app.jobs import queue, tasks


def test_inline_executor_runs_existing_enrichment_function_in_a_thread(monkeypatch) -> None:
    session_id = uuid4()
    monkeypatch.setattr(queue, "_run_inline_enrichment", lambda received: {"session": str(received)})

    result = asyncio.run(queue.InlineEnrichmentJobExecutor().execute(session_id))

    assert result == {"mode": "inline", "result": {"session": str(session_id)}}


def test_arq_executor_rejects_missing_redis(monkeypatch) -> None:
    monkeypatch.setattr(queue.settings, "redis_url", "")

    try:
        asyncio.run(queue.ArqEnrichmentJobExecutor().execute(uuid4()))
    except RuntimeError as exc:
        assert "REDIS_URL" in str(exc)
    else:  # pragma: no cover - test failure guard
        raise AssertionError("ARQ execution unexpectedly ran without Redis")


def test_arq_task_delegates_to_the_durable_job_worker(monkeypatch) -> None:
    job_id = uuid4()
    received = {}

    def fake_run(payload, _task_header, _db):
        received["job_id"] = payload.job_id
        return {"job_id": str(payload.job_id), "status": "completed"}

    monkeypatch.setattr(tasks, "run_enrichment_task", fake_run)

    result = asyncio.run(tasks.run_session_enrichment({}, str(job_id)))

    assert received["job_id"] == job_id
    assert result == {"job_id": str(job_id), "status": "completed"}
