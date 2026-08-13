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
    async def execute(self, target_id: UUID) -> dict:
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

    async def execute(self, job_id: UUID) -> dict:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required when JOB_EXECUTION_MODE=arq")
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            job = await redis.enqueue_job("run_session_enrichment", str(job_id))
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


async def enqueue_enrichment(job_id: UUID) -> dict:
    """Dispatch an already-persisted enrichment job through the selected executor."""
    try:
        return await get_enrichment_job_executor().execute(job_id)
    except Exception as exc:
        logger.error(
            "enrichment_dispatch_failed",
            execution_mode=settings.job_execution_mode,
            error_category="job_execution",
            error_type=type(exc).__name__,
        )
        raise RuntimeError("Enrichment execution could not be started") from None
