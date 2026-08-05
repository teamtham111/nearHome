"""Execution mode behavior independent of Redis and ARQ infrastructure."""

import asyncio
from uuid import uuid4

from app.jobs import queue


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
