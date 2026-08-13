"""Background job tasks for ARQ worker."""

from __future__ import annotations

from uuid import UUID

from app.db.session import SessionLocal
from app.worker_main import CloudTaskPayload, run_enrichment_task


async def run_session_enrichment(ctx: dict, job_id: str) -> dict:
    """ARQ task: run one durable enrichment job and persist its progress."""
    db = SessionLocal()
    try:
        return run_enrichment_task(CloudTaskPayload(job_id=UUID(job_id)), None, db)
    finally:
        db.close()


TASKS = [run_session_enrichment]
