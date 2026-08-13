"""Transactional persistence for durable enrichment jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.orm import EnrichmentJobORM

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
SESSION_ENRICHMENT_JOB = "SESSION_ENRICHMENT"


class EnrichmentJobRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_or_get_active(self, session_id: UUID) -> tuple[EnrichmentJobORM, bool]:
        """Create one session job, returning an existing active job on retries.

        The partial unique index is the cross-instance duplicate guard. The
        IntegrityError branch handles a second API instance winning the race.
        """
        active = self._active_for_session(session_id)
        if active is not None:
            return active, False

        job = EnrichmentJobORM(
            session_id=session_id,
            job_type=SESSION_ENRICHMENT_JOB,
            status="queued",
            progress_stage="queued",
            attempts=0,
        )
        self.db.add(job)
        try:
            self.db.commit()
            self.db.refresh(job)
            return job, True
        except IntegrityError:
            self.db.rollback()
            active = self._active_for_session(session_id)
            if active is None:  # pragma: no cover - protects an unexpected database race
                raise
            return active, False

    def get(self, job_id: UUID) -> EnrichmentJobORM | None:
        return self.db.get(EnrichmentJobORM, job_id)

    def get_for_session(self, job_id: UUID, session_id: UUID) -> EnrichmentJobORM | None:
        return (
            self.db.query(EnrichmentJobORM)
            .filter(EnrichmentJobORM.id == job_id, EnrichmentJobORM.session_id == session_id)
            .first()
        )

    def claim(self, job_id: UUID) -> EnrichmentJobORM | None:
        """Atomically transition one queued job to running.

        A duplicate Cloud Task never gets past this conditional update. It can
        safely return 2xx after observing the stored running/completed state.
        """
        now = datetime.now(UTC)
        result = self.db.execute(
            update(EnrichmentJobORM)
            .where(EnrichmentJobORM.id == job_id, EnrichmentJobORM.status == "queued")
            .values(
                status="running",
                progress_stage="starting",
                attempts=EnrichmentJobORM.attempts + 1,
                started_at=now,
                updated_at=now,
                error_code=None,
                error_message=None,
            )
        )
        self.db.commit()
        if result.rowcount != 1:
            return None
        return self.get(job_id)

    def requeue_if_stale(self, job_id: UUID, stale_before: datetime) -> bool:
        """Recover a running job only after its worker lease has clearly expired."""
        result = self.db.execute(
            update(EnrichmentJobORM)
            .where(
                EnrichmentJobORM.id == job_id,
                EnrichmentJobORM.status == "running",
                EnrichmentJobORM.updated_at < stale_before,
            )
            .values(status="queued", progress_stage="retrying", updated_at=datetime.now(UTC))
        )
        self.db.commit()
        return result.rowcount == 1

    def fail_if_stale(self, job_id: UUID, stale_before: datetime) -> bool:
        """Terminate abandoned active work so clients cannot poll forever.

        Cloud Tasks normally redelivers an interrupted request, but a task can
        also be exhausted or removed outside this process. Polling and a new
        start request use this bounded fallback after the configured worker
        lease has expired.
        """
        now = datetime.now(UTC)
        result = self.db.execute(
            update(EnrichmentJobORM)
            .where(
                EnrichmentJobORM.id == job_id,
                EnrichmentJobORM.status.in_(ACTIVE_STATUSES),
                EnrichmentJobORM.updated_at < stale_before,
            )
            .values(
                status="failed",
                progress_stage="failed",
                completed_at=now,
                updated_at=now,
                error_code="enrichment_job_timed_out",
                error_message="Enrichment did not finish in time. Please start it again.",
                internal_error_detail="Job exceeded the configured stale-job timeout.",
            )
        )
        self.db.commit()
        return result.rowcount == 1

    def fail_stale_active_for_session(self, session_id: UUID, stale_before: datetime) -> bool:
        """Expire an abandoned session job before allowing a fresh request."""
        active = self._active_for_session(session_id)
        return bool(active and self.fail_if_stale(active.id, stale_before))

    def set_stage(self, job_id: UUID, stage: str) -> None:
        self._update(job_id, progress_stage=stage)

    def complete(self, job_id: UUID, result: dict[str, object]) -> None:
        now = datetime.now(UTC)
        self._update(
            job_id,
            status="completed",
            progress_stage="completed",
            completed_at=now,
            result_json=result,
            error_code=None,
            error_message=None,
            internal_error_detail=None,
        )

    def fail_permanently(
        self,
        job_id: UUID,
        *,
        error_code: str,
        safe_message: str,
        internal_detail: str | None = None,
    ) -> None:
        self._terminal_failure(job_id, error_code, safe_message, internal_detail)

    def requeue_retryable(
        self,
        job_id: UUID,
        *,
        error_code: str,
        safe_message: str,
        internal_detail: str | None = None,
    ) -> None:
        self._update(
            job_id,
            status="queued",
            progress_stage="retrying",
            error_code=error_code,
            error_message=safe_message,
            internal_error_detail=_truncate(internal_detail),
        )

    def mark_enqueue_failed(self, job_id: UUID, internal_detail: str | None = None) -> None:
        self._terminal_failure(
            job_id,
            "task_enqueue_failed",
            "Enrichment could not be queued. Please try again.",
            internal_detail,
        )

    def _terminal_failure(self, job_id: UUID, error_code: str, safe_message: str, internal_detail: str | None) -> None:
        self._update(
            job_id,
            status="failed",
            progress_stage="failed",
            completed_at=datetime.now(UTC),
            error_code=error_code,
            error_message=safe_message,
            internal_error_detail=_truncate(internal_detail),
        )

    def _update(self, job_id: UUID, **values: object) -> None:
        values["updated_at"] = datetime.now(UTC)
        self.db.execute(update(EnrichmentJobORM).where(EnrichmentJobORM.id == job_id).values(**values))
        self.db.commit()

    def _active_for_session(self, session_id: UUID) -> EnrichmentJobORM | None:
        return (
            self.db.query(EnrichmentJobORM)
            .filter(
                EnrichmentJobORM.session_id == session_id,
                EnrichmentJobORM.job_type == SESSION_ENRICHMENT_JOB,
                EnrichmentJobORM.status.in_(ACTIVE_STATUSES),
            )
            .order_by(EnrichmentJobORM.created_at.desc())
            .first()
        )


def _truncate(value: str | None, length: int = 2000) -> str | None:
    return value[:length] if value else None
