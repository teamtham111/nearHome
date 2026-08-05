"""Enqueue background jobs with inline fallback when Redis is unavailable."""

from __future__ import annotations

from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def enqueue_enrichment(session_id: UUID) -> dict:
    """Try ARQ queue; fall back to synchronous execution."""
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
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        job = await redis.enqueue_job("run_session_enrichment", str(session_id))
        await redis.close()
        if job:
            return {"mode": "queued", "job_id": job.job_id}
    except Exception as exc:
        # Connection exceptions can embed a Redis URL, which may contain a
        # password. Log the failure category, never the exception text.
        logger.warning("redis_enqueue_failed", error_category="redis", error_type=type(exc).__name__)

    # Inline fallback for dev without Redis
    from app.services.enrichment_service import EnrichmentService

    db = SessionLocal()
    try:
        result = EnrichmentService(db).run_session_enrichment(session_id, simulate_delay=False)
        return {"mode": "inline", "result": result}
    finally:
        db.close()
