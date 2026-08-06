"""Unit tests for Cloud Tasks dispatch and private-worker guards."""

from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs.cloud_tasks import CloudTasksDispatcher, TaskDispatchError
from app.worker_main import require_cloud_tasks_header


class FakeTasksClient:
    def __init__(self) -> None:
        self.parent: str | None = None
        self.task = None

    @staticmethod
    def queue_path(project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def create_task(self, *, parent: str, task):
        self.parent = parent
        self.task = task
        return type("Created", (), {"name": task.name})()


def test_cloud_tasks_dispatcher_enqueues_only_the_job_id(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gcp_project_id", "test-project")
    monkeypatch.setattr(settings, "cloud_tasks_location", "asia-southeast1")
    monkeypatch.setattr(settings, "cloud_tasks_queue", "nearhome-enrichment")
    monkeypatch.setattr(settings, "enrichment_worker_url", "https://worker.example.test")
    monkeypatch.setattr(settings, "cloud_tasks_service_account_email", "tasks@example.test")
    fake = FakeTasksClient()
    job_id = uuid4()

    created = CloudTasksDispatcher(client=fake).enqueue_enrichment(job_id)

    assert created.name.endswith(f"enrichment-{job_id}")
    assert fake.parent == "projects/test-project/locations/asia-southeast1/queues/nearhome-enrichment"
    assert fake.task.http_request.url == "https://worker.example.test/internal/tasks/enrichment"
    assert fake.task.http_request.body == f'{{"job_id": "{job_id}"}}'.encode()
    assert fake.task.http_request.oidc_token.service_account_email == "tasks@example.test"


def test_dispatcher_fails_without_required_task_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gcp_project_id", "")
    with pytest.raises(TaskDispatchError, match="not configured"):
        CloudTasksDispatcher(client=FakeTasksClient()).enqueue_enrichment(uuid4())


def test_private_worker_rejects_missing_cloud_tasks_header_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(Exception) as exc_info:
        require_cloud_tasks_header(None)
    assert getattr(exc_info.value, "status_code", None) == 403
