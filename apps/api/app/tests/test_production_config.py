"""Production-only configuration checks stay explicit and fail closed."""

import pytest

from app.core.config import Settings
from app.db.session import build_engine_options


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "demo_mode": False,
        "web_url": "https://app.example.test",
        "cors_origins": "https://app.example.test",
        "database_url": "postgresql+psycopg://user:password@database:5432/nearhome",
        "secret_key": "x" * 32,
        "google_maps_api_key": "test-google-key",
        "onemap_email": "test@example.test",
        "onemap_password": "test-password",
        "groq_api_key": "test-groq-key",
        "job_execution_mode": "cloud_tasks",
        "gcp_project_id": "test-project",
        "cloud_tasks_location": "asia-southeast1",
        "cloud_tasks_queue": "nearhome-enrichment",
        "enrichment_worker_url": "https://worker.example.test",
        "cloud_tasks_service_account_email": "tasks@example.test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_valid_production_settings_pass_validation() -> None:
    _production_settings().validate_production()


def test_inline_execution_allows_an_absent_redis_url_locally() -> None:
    Settings(
        _env_file=None,
        app_env="development",
        job_execution_mode="inline",
        redis_url="",
    ).validate_production()


def test_plain_supabase_postgres_url_uses_installed_psycopg_driver() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://user:password@aws-region.pooler.supabase.com:5432/postgres?sslmode=require",
    )
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_production_postgres_connections_require_ssl() -> None:
    options = build_engine_options("postgresql+psycopg://user:password@database:5432/nearhome", production=True)
    assert options["connect_args"] == {"connect_timeout": 5, "sslmode": "require"}


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"demo_mode": True}, "DEMO_MODE must be false"),
        ({"cors_origins": "http://localhost:3000"}, "CORS_ORIGINS must contain only"),
        ({"cors_origins": "https://other.example.test"}, "CORS_ORIGINS must include WEB_URL"),
        ({"database_url": "postgresql+psycopg://user:password@localhost:5432/nearhome"}, "DATABASE_URL"),
        ({"google_maps_api_key": ""}, "GOOGLE_MAPS_API_KEY"),
        ({"job_execution_mode": "arq", "redis_url": ""}, "REDIS_URL is required"),
        ({"job_execution_mode": "inline"}, "JOB_EXECUTION_MODE must be cloud_tasks"),
        ({"enrichment_worker_url": ""}, "ENRICHMENT_WORKER_URL"),
    ],
)
def test_invalid_production_settings_fail_closed(overrides: dict[str, object], expected_message: str) -> None:
    with pytest.raises(RuntimeError, match=expected_message):
        _production_settings(**overrides).validate_production()
