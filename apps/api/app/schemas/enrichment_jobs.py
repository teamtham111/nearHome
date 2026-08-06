"""Public-safe schemas for durable enrichment jobs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class EnrichmentJobStartResponse(BaseModel):
    job_id: UUID
    status: str
    status_url: str


class EnrichmentJobStatusResponse(BaseModel):
    job_id: UUID
    session_id: UUID
    status: str
    progress_stage: str
    attempts: int
    error_code: str | None = None
    error_message: str | None = None
    result_available: bool
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime
