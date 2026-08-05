"""Application configuration and production safety checks."""

from pathlib import Path
from urllib.parse import urlparse

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
    api_port: int = 8000
    web_url: str = "http://localhost:3000"
    # Support both common loopback browser URLs for local development. Deployments
    # should override this with their exact frontend origin(s).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = "postgresql+psycopg://nearhome:nearhome@localhost:5432/nearhome"
    redis_url: str = "redis://localhost:6379/0"

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
        if not self.is_production:
            return

        problems: list[str] = []
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
        # Redis is intentionally not a startup requirement: enrichment can fall
        # back inline, and /ready reports a degraded state if it is unavailable.
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
