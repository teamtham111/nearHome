"""Dispatch enrichment through an explicit inline or ARQ executor."""

from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_enrichment_slots = asyncio.Semaphore(settings.max_concurrent_enrichments)
_active_session_ids: set[UUID] = set()
_active_sessions_lock = threading.Lock()


class EnrichmentJobExecutor(ABC):
    """Small execution boundary that keeps enrichment business logic shared."""

    @abstractmethod
    async def execute(self, session_id: UUID) -> dict:
        raise NotImplementedError


class InlineEnrichmentJobExecutor(EnrichmentJobExecutor):
    """Run one bounded enrichment request inside the Cloud Run API instance."""

    async def execute(self, session_id: UUID) -> dict:
        with _active_sessions_lock:
            if session_id in _active_session_ids:
                return {"mode": "inline", "status": "already_running"}
            _active_session_ids.add(session_id)
        try:
            async with _enrichment_slots:
                result = await asyncio.to_thread(_run_inline_enrichment, session_id)
            return {"mode": "inline", "result": result}
        finally:
            with _active_sessions_lock:
                _active_session_ids.discard(session_id)


class ArqEnrichmentJobExecutor(EnrichmentJobExecutor):
    """Optional local/future-scale executor; requires configured Redis."""

    async def execute(self, session_id: UUID) -> dict:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required when JOB_EXECUTION_MODE=arq")
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            job = await redis.enqueue_job("run_session_enrichment", str(session_id))
        finally:
            await redis.close()
        if not job:
            raise RuntimeError("ARQ could not enqueue enrichment")
        return {"mode": "queued", "job_id": job.job_id}


def get_enrichment_job_executor() -> EnrichmentJobExecutor:
    if settings.job_execution_mode == "inline":
        return InlineEnrichmentJobExecutor()
    if settings.job_execution_mode == "arq":
        return ArqEnrichmentJobExecutor()
    raise RuntimeError("JOB_EXECUTION_MODE must be either inline or arq")


def _run_inline_enrichment(session_id: UUID) -> dict:
    from app.db.session import SessionLocal
    from app.services.enrichment_service import EnrichmentService

    db = SessionLocal()
    try:
        return EnrichmentService(db).run_session_enrichment(session_id, simulate_delay=False)
    finally:
        db.close()


async def enqueue_enrichment(session_id: UUID) -> dict:
    """Record a new run and dispatch it through the configured executor."""
    # Existing rows may be SUCCEEDED from an earlier run. Mark them before the
    # job is enqueued so the browser cannot mistake stale completion for this run.
    from app.db.session import SessionLocal
    from app.repositories.enrichment_repository import EnrichmentRepository
    from app.repositories.session_repository import SessionRepository

    status_db = SessionLocal()
    try:
        session = SessionRepository(status_db).get_session(session_id)
        if session is None:
            raise ValueError("Session not found")
        EnrichmentRepository(status_db).mark_runs_queued([listing.id for listing in session.listings])
    finally:
        status_db.close()

    try:
        return await get_enrichment_job_executor().execute(session_id)
    except Exception as exc:
        logger.error(
            "enrichment_dispatch_failed",
            execution_mode=settings.job_execution_mode,
            error_category="job_execution",
            error_type=type(exc).__name__,
        )
        failure_db = SessionLocal()
        try:
            failed_session = SessionRepository(failure_db).get_session(session_id)
            if failed_session is not None:
                EnrichmentRepository(failure_db).mark_queued_runs_failed(
                    [listing.id for listing in failed_session.listings],
                    "Enrichment could not be started. Please retry.",
                )
        finally:
            failure_db.close()
        raise RuntimeError("Enrichment execution could not be started") from None
