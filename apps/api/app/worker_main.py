"""Private Cloud Run application that executes durable enrichment jobs."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.base import AdapterError
from app.adapters.transport_data.major_road_network import validate_major_road_mapping_artifacts
from app.core.config import settings
from app.core.logging import RequestLoggingMiddleware, configure_logging, get_logger
from app.db.session import get_db
from app.models.orm import EnrichmentJobORM
from app.repositories.enrichment_job_repository import EnrichmentJobRepository
from app.services.enrichment_service import EnrichmentService

logger = get_logger(__name__)


class CloudTaskPayload(BaseModel):
    job_id: UUID


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    settings.validate_production()
    if not settings.demo_mode:
        validate_major_road_mapping_artifacts()
    get_logger(__name__).info("enrichment_worker_started", app_env=settings.app_env)
    yield


worker_app = FastAPI(
    title="NearHome Enrichment Worker",
    description="Private Cloud Tasks target for NearHome enrichment jobs",
    version="0.1.0",
    lifespan=lifespan,
)
worker_app.add_middleware(RequestLoggingMiddleware)


def require_cloud_tasks_header(x_cloudtasks_taskname: str | None = Header(default=None)) -> None:
    """Defence in depth; Cloud Run IAM remains the actual public-access gate."""
    if settings.is_production and not x_cloudtasks_taskname:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Task authentication required")


@worker_app.post("/internal/tasks/enrichment")
def run_enrichment_task(
    payload: CloudTaskPayload,
    _task_header: None = Depends(require_cloud_tasks_header),
    db: Session = Depends(get_db),
) -> dict:
    repo = EnrichmentJobRepository(db)
    existing = repo.get(payload.job_id)
    if existing is None:
        # A user may delete a session after its task was dispatched. Returning
        # success prevents retries against intentionally deleted state.
        return {"job_id": str(payload.job_id), "status": "missing"}
    if existing.status in {"completed", "failed", "cancelled"}:
        return {"job_id": str(payload.job_id), "status": existing.status}
    if existing.status == "running":
        stale_before = datetime.now(UTC) - timedelta(seconds=settings.enrichment_job_stale_seconds)
        if not repo.requeue_if_stale(payload.job_id, stale_before):
            return {"job_id": str(payload.job_id), "status": "already_running"}

    job = repo.claim(payload.job_id)
    if job is None:
        current = repo.get(payload.job_id)
        return {"job_id": str(payload.job_id), "status": current.status if current else "missing"}

    started = time.perf_counter()
    logger.info(
        "enrichment_job_running",
        job_id=str(job.id),
        session_id=str(job.session_id),
        attempt=job.attempts,
        stage=job.progress_stage,
    )
    try:
        result = EnrichmentService(db).run_session_enrichment(
            job.session_id,
            simulate_delay=False,
            progress_callback=lambda stage: repo.set_stage(job.id, stage),
            job_id=job.id,
        )
    except ValueError as exc:
        repo.fail_permanently(
            job.id,
            error_code="invalid_job_input",
            safe_message="This enrichment job can no longer be completed. Please start it again.",
            internal_detail=str(exc),
        )
        logger.warning(
            "enrichment_job_failed_permanently",
            job_id=str(job.id),
            session_id=str(job.session_id),
            attempt=job.attempts,
            error_category="invalid_input",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return {"job_id": str(job.id), "status": "failed"}
    except Exception as exc:
        _handle_worker_exception(repo, job, exc, started)
        # Non-2xx asks Cloud Tasks to retry only failures classified as transient.
        if _is_retryable(exc) and job.attempts < settings.max_enrichment_job_attempts:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Retry requested") from None
        return {"job_id": str(job.id), "status": "failed"}

    safe_result = {
        "listing_count": len(result.get("listings", [])),
        "error_count": len(result.get("errors", [])),
    }
    repo.complete(job.id, safe_result)
    logger.info(
        "enrichment_job_completed",
        job_id=str(job.id),
        session_id=str(job.session_id),
        attempt=job.attempts,
        stage="completed",
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return {"job_id": str(job.id), "status": "completed"}


def _handle_worker_exception(
    repo: EnrichmentJobRepository,
    job: EnrichmentJobORM,
    exc: Exception,
    started: float,
) -> None:
    retryable = _is_retryable(exc) and job.attempts < settings.max_enrichment_job_attempts
    if retryable:
        repo.requeue_retryable(
            job.id,
            error_code="temporary_provider_failure",
            safe_message="Enrichment is temporarily unavailable. Retrying automatically.",
            internal_detail=str(exc),
        )
        outcome = "retrying"
    else:
        repo.fail_permanently(
            job.id,
            error_code="enrichment_failed",
            safe_message="Enrichment could not be completed. Please try again.",
            internal_detail=str(exc),
        )
        outcome = "failed"
    logger.error(
        "enrichment_job_execution_failed",
        job_id=str(job.id),
        session_id=str(job.session_id),
        attempt=job.attempts,
        stage=job.progress_stage,
        outcome=outcome,
        error_category="retryable" if retryable else "permanent",
        error_type=type(exc).__name__,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.TransportError, ConnectionError)):
        return True
    return isinstance(exc, AdapterError) and exc.error_code in {"timeout", "temporarily_unavailable", "rate_limit"}
