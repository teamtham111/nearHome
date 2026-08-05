"""Database session management."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


def build_engine_options(database_url: str, *, production: bool) -> dict[str, object]:
    """Return bounded SQLAlchemy options without opening a DB connection."""
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if not database_url.startswith("postgresql+"):
        return engine_options

    # Cloud Run may scale to zero and Supabase has bounded connection quotas.
    # Creating an Engine is lazy; these settings do not open a connection at
    # import time and keep each API container's pool deliberately small.
    connect_args: dict[str, object] = {"connect_timeout": 5}
    if production:
        # Supabase application traffic must not downgrade to a plaintext
        # PostgreSQL connection, even if a pasted connection URL omits it.
        connect_args["sslmode"] = "require"
    engine_options.update(
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_recycle=settings.database_pool_recycle_seconds,
        pool_timeout=10,
        connect_args=connect_args,
    )
    return engine_options


engine = create_engine(
    settings.database_url,
    **build_engine_options(settings.database_url, production=settings.is_production),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
