"""ARQ worker entry point."""

from __future__ import annotations

from arq.connections import RedisSettings

from app.core.config import settings
from app.jobs.tasks import TASKS

if settings.job_execution_mode != "arq":
    raise RuntimeError("The ARQ worker requires JOB_EXECUTION_MODE=arq")
if not settings.redis_url:
    raise RuntimeError("REDIS_URL is required when JOB_EXECUTION_MODE=arq")


class WorkerSettings:
    functions = TASKS
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600


if __name__ == "__main__":
    from arq import run_worker

    run_worker(WorkerSettings)
