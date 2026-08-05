"""Background job tasks for ARQ worker."""

from __future__ import annotations

from uuid import UUID

from app.db.session import SessionLocal
from app.services.enrichment_service import EnrichmentService


async def run_session_enrichment(ctx: dict, session_id: str) -> dict:
    """ARQ task: run full enrichment for a comparison session."""
    db = SessionLocal()
    try:
        service = EnrichmentService(db)
        result = service.run_session_enrichment(UUID(session_id), simulate_delay=False)
        return {"session_id": session_id, "status": "completed", "result": result}
    finally:
        db.close()


TASKS = [run_session_enrichment]
