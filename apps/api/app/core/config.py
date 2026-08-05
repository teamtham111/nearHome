"""Application configuration and production safety checks."""

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
    job_execution_mode: str = "inline"
    max_concurrent_enrichments: int = 1

    enable_playwright_fallback: bool = True
    playwright_timeout_seconds: int = 25
    playwright_max_concurrency: int = 1

    google_maps_api_key: str = ""
    onemap_email: str = ""
    onemap_password: str = ""
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

    def validate_production(self) -> None:
        """Fail closed when a production process is configured unsafely.

        Values themselves are deliberately never included in the error text or
        application logs.  This runs before the API begins accepting traffic.
        """
        problems: list[str] = []
        if self.job_execution_mode not in {"inline", "arq"}:
            problems.append("JOB_EXECUTION_MODE must be either inline or arq")
        if self.job_execution_mode == "arq" and not self.redis_url:
            problems.append("REDIS_URL is required when JOB_EXECUTION_MODE=arq")
        if self.max_concurrent_enrichments < 1:
            problems.append("MAX_CONCURRENT_ENRICHMENTS must be at least 1")
        if self.playwright_timeout_seconds < 1 or self.playwright_max_concurrency < 1:
            problems.append("Playwright timeout and concurrency must both be at least 1")
        if not self.is_production:
            if problems:
                raise RuntimeError("Invalid runtime configuration: " + "; ".join(problems))
            return

        if self.demo_mode:
            problems.append("DEMO_MODE must be false")
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

        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))


def _is_loopback_url(value: str) -> bool:
    """Return whether a connection URL is visibly targeted at this machine."""
    hostname = urlparse(value).hostname
    return hostname in {"localhost", "127.0.0.1", "::1"}


settings = Settings()
