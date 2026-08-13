"""Application configuration and production safety checks."""

import re
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root .env — works whether API is started from repo root or apps/api
_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    demo_mode: bool = True
    log_level: str = "INFO"
    # Injected when the immutable container image is built. It deliberately
    # has a safe fallback so health checks never fail for a locally built image.
    git_sha: str = "unknown"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    web_url: str = "http://localhost:3000"
    # Support both common loopback browser URLs for local development. Deployments
    # should override this with their exact frontend origin(s).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = "postgresql+psycopg://nearhome:nearhome@localhost:5432/nearhome"
    database_pool_size: int = 3
    database_max_overflow: int = 2
    database_pool_recycle_seconds: int = 300
    redis_url: str = ""
    route_cache_namespace: str = "nearhome:routes:v1"
    route_request_concurrency: int = 4
    job_execution_mode: str = "inline"
    max_concurrent_enrichments: int = 1
    gcp_project_id: str = ""
    cloud_tasks_location: str = "asia-southeast1"
    cloud_tasks_queue: str = "nearhome-enrichment"
    enrichment_worker_url: str = ""
    cloud_tasks_service_account_email: str = ""
    cloud_tasks_oidc_audience: str = ""
    cloud_tasks_dispatch_deadline_seconds: int = 600
    max_enrichment_job_attempts: int = 3
    enrichment_job_stale_seconds: int = 660

    enable_playwright_fallback: bool = True
    playwright_timeout_seconds: int = 25
    playwright_max_concurrency: int = 1
    # Disabled by default. When explicitly enabled on a tagged test revision,
    # this exposes only fixed, non-sensitive egress diagnostics behind a token.
    enable_egress_diagnostics: bool = False
    egress_diagnostics_token: str = ""

    google_maps_api_key: str = ""
    fair_price_model_artifact_path: str = ""
    onemap_email: str = ""
    onemap_password: str = ""
    # Offline major-road artifacts. The build pipeline obtains the official
    # SLA geometry and OSM graph; enrichment must never download either.
    sla_major_roads_path: str = ""
    singapore_drive_graph_path: str = ""
    sla_osm_major_road_mapping_path: str = ""
    lta_account_key: str = ""
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"

    # Official HDB/data.gov.sg carpark sources. The availability feed is
    # public, but an optional data.gov.sg key raises production rate limits.
    data_gov_sg_api_key: str = ""
    hdb_carpark_availability_url: str = "https://api.data.gov.sg/v1/transport/carpark-availability"
    hdb_carpark_availability_cache_seconds: int = 60
    hdb_carpark_availability_stale_minutes: int = 15
    hdb_carpark_history_min_samples: int = 5
    hdb_carpark_static_source_updated_at: str | None = None

    smart_paste_prompt_version: str = "1.0.0"
    smart_paste_schema_version: str = "1.0.0"
    smart_paste_pipeline_version: str = "1.0.0"

    secret_key: str = "dev-secret-change-in-production"
    rate_limit_per_minute: int = 60

    requirement_rule_version: str = "1.0.0"
    scoring_version: str = "2.0.0"
    recommendation_rule_version: str = "1.0.0"

    @field_validator("database_url")
    @classmethod
    def normalize_postgres_driver(cls, value: str) -> str:
        """Use the installed psycopg v3 driver for ordinary Supabase URLs."""
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.casefold() == "production"

    @property
    def release_sha(self) -> str:
        return self.git_sha.strip() or "unknown"

    def validate_production(self) -> None:
        """Fail closed when a production process is configured unsafely.

        Values themselves are deliberately never included in the error text or
        application logs.  This runs before the API begins accepting traffic.
        """
        problems: list[str] = []
        if self.job_execution_mode not in {"inline", "arq", "cloud_tasks"}:
            problems.append("JOB_EXECUTION_MODE must be inline, arq, or cloud_tasks")
        if self.job_execution_mode == "arq" and not self.redis_url:
            problems.append("REDIS_URL is required when JOB_EXECUTION_MODE=arq")
        if self.cloud_tasks_dispatch_deadline_seconds < 15:
            problems.append("CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS must be at least 15")
        if self.max_enrichment_job_attempts < 1:
            problems.append("MAX_ENRICHMENT_JOB_ATTEMPTS must be at least 1")
        if self.enrichment_job_stale_seconds <= self.cloud_tasks_dispatch_deadline_seconds:
            problems.append("ENRICHMENT_JOB_STALE_SECONDS must exceed the Cloud Tasks dispatch deadline")
        if self.max_concurrent_enrichments < 1:
            problems.append("MAX_CONCURRENT_ENRICHMENTS must be at least 1")
        if self.route_request_concurrency < 1:
            problems.append("ROUTE_REQUEST_CONCURRENCY must be at least 1")
        if self.playwright_timeout_seconds < 1 or self.playwright_max_concurrency < 1:
            problems.append("Playwright timeout and concurrency must both be at least 1")
        if not self.is_production:
            if problems:
                raise RuntimeError("Invalid runtime configuration: " + "; ".join(problems))
            return

        if self.demo_mode:
            problems.append("DEMO_MODE must be false")
        if not re.fullmatch(r"[0-9a-f]{40}", self.release_sha, flags=re.IGNORECASE):
            problems.append("GIT_SHA must be the full 40-character commit SHA when APP_ENV=production")
        if self.secret_key == "dev-secret-change-in-production" or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be a unique value of at least 32 characters")
        if not self.web_url.startswith("https://"):
            problems.append("WEB_URL must be the HTTPS production frontend origin")
        if not self.cors_origin_list:
            problems.append("CORS_ORIGINS must contain the production frontend origin")
        for origin in self.cors_origin_list:
            parsed = urlparse(origin)
            if parsed.scheme != "https" or not parsed.netloc or parsed.hostname in {"localhost", "127.0.0.1"}:
                problems.append("CORS_ORIGINS must contain only explicit HTTPS non-local origins")
                break
        if self.web_url.rstrip("/") not in {origin.rstrip("/") for origin in self.cors_origin_list}:
            problems.append("CORS_ORIGINS must include WEB_URL")
        if _is_loopback_url(self.database_url):
            problems.append("DATABASE_URL must not point to a loopback host")
        if not self.google_maps_api_key:
            problems.append("GOOGLE_MAPS_API_KEY is required when APP_ENV=production")
        if not self.onemap_email or not self.onemap_password:
            problems.append("ONEMAP_EMAIL and ONEMAP_PASSWORD are required when APP_ENV=production")
        if not self.groq_api_key:
            problems.append("GROQ_API_KEY is required when APP_ENV=production")
        if self.job_execution_mode != "cloud_tasks":
            problems.append("JOB_EXECUTION_MODE must be cloud_tasks when APP_ENV=production")
        required_task_settings = {
            "GCP_PROJECT_ID": self.gcp_project_id,
            "CLOUD_TASKS_LOCATION": self.cloud_tasks_location,
            "CLOUD_TASKS_QUEUE": self.cloud_tasks_queue,
            "ENRICHMENT_WORKER_URL": self.enrichment_worker_url,
            "CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL": self.cloud_tasks_service_account_email,
        }
        for name, value in required_task_settings.items():
            if not value:
                problems.append(f"{name} is required when APP_ENV=production")
        if self.enrichment_worker_url and not self.enrichment_worker_url.startswith("https://"):
            problems.append("ENRICHMENT_WORKER_URL must be an HTTPS URL")
        if self.enable_egress_diagnostics and len(self.egress_diagnostics_token) < 32:
            problems.append(
                "EGRESS_DIAGNOSTICS_TOKEN must be a unique value of at least 32 characters when diagnostics are enabled"
            )

        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))


def _is_loopback_url(value: str) -> bool:
    """Return whether a connection URL is visibly targeted at this machine."""
    hostname = urlparse(value).hostname
    return hostname in {"localhost", "127.0.0.1", "::1"}


settings = Settings()
