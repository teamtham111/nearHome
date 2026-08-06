"""Google Cloud Tasks dispatch for durable enrichment work."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import UUID

from google.cloud import tasks_v2
from google.protobuf import duration_pb2

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class TaskDispatchError(RuntimeError):
    """A safe boundary between the public API and Cloud Tasks."""


@dataclass(frozen=True)
class CreatedTask:
    name: str


class CloudTasksDispatcher:
    def __init__(self, client: tasks_v2.CloudTasksClient | None = None) -> None:
        self.client = client or tasks_v2.CloudTasksClient()

    def enqueue_enrichment(self, job_id: UUID) -> CreatedTask:
        if not (
            settings.gcp_project_id and settings.enrichment_worker_url and settings.cloud_tasks_service_account_email
        ):
            raise TaskDispatchError("Cloud Tasks is not configured")

        queue_path = self.client.queue_path(
            settings.gcp_project_id,
            settings.cloud_tasks_location,
            settings.cloud_tasks_queue,
        )
        task_id = f"enrichment-{job_id}"
        audience = settings.cloud_tasks_oidc_audience or settings.enrichment_worker_url
        task = tasks_v2.Task(
            name=f"{queue_path}/tasks/{task_id}",
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=f"{settings.enrichment_worker_url.rstrip('/')}/internal/tasks/enrichment",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"job_id": str(job_id)}).encode("utf-8"),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=settings.cloud_tasks_service_account_email,
                    audience=audience,
                ),
            ),
            dispatch_deadline=duration_pb2.Duration(seconds=settings.cloud_tasks_dispatch_deadline_seconds),
        )
        try:
            created = self.client.create_task(parent=queue_path, task=task)
        except Exception as exc:  # provider errors must not leak through the public API
            # Cloud Tasks treats a duplicate deterministic task name as a success
            # for this job: a task already exists and will execute the same idempotent job.
            if _is_already_exists(exc):
                logger.info("enrichment_task_already_exists", job_id=str(job_id), task_name=task_id)
                return CreatedTask(name=task_id)
            logger.error(
                "enrichment_task_enqueue_failed",
                job_id=str(job_id),
                error_category="cloud_tasks",
                error_type=type(exc).__name__,
            )
            raise TaskDispatchError("Cloud Tasks could not accept the enrichment job") from exc

        logger.info("enrichment_task_enqueued", job_id=str(job_id), task_name=created.name)
        return CreatedTask(name=created.name)


def get_cloud_tasks_dispatcher() -> CloudTasksDispatcher:
    return CloudTasksDispatcher()


def _is_already_exists(exc: Exception) -> bool:
    return bool(re.search(r"already exists|AlreadyExists", str(exc), re.IGNORECASE))
