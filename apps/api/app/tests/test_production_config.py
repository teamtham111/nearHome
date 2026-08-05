"""Production-only configuration checks stay explicit and fail closed."""

import pytest

from app.core.config import Settings


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
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_valid_production_settings_pass_validation() -> None:
    _production_settings().validate_production()


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"demo_mode": True}, "DEMO_MODE must be false"),
        ({"cors_origins": "http://localhost:3000"}, "CORS_ORIGINS must contain only"),
        ({"cors_origins": "https://other.example.test"}, "CORS_ORIGINS must include WEB_URL"),
        ({"database_url": "postgresql+psycopg://user:password@localhost:5432/nearhome"}, "DATABASE_URL"),
        ({"google_maps_api_key": ""}, "GOOGLE_MAPS_API_KEY"),
    ],
)
def test_invalid_production_settings_fail_closed(overrides: dict[str, object], expected_message: str) -> None:
    with pytest.raises(RuntimeError, match=expected_message):
        _production_settings(**overrides).validate_production()
