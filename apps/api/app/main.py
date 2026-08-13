"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.transport_data.major_road_network import validate_major_road_mapping_artifacts
from app.api.routes import router
from app.core.config import settings
from app.core.logging import RequestLoggingMiddleware, configure_logging, get_logger
from app.core.rate_limit import RateLimitMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    settings.validate_production()
    if not settings.demo_mode:
        validate_major_road_mapping_artifacts()
    get_logger(__name__).info(
        "api_started",
        app_env=settings.app_env,
        demo_mode=settings.demo_mode,
        job_execution_mode=settings.job_execution_mode,
        cors_origin_count=len(settings.cors_origin_list),
    )
    yield


app = FastAPI(
    title="NearHome API",
    description="Decision-support API for Singapore HDB resale buyers",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID"],
)
app.add_middleware(RateLimitMiddleware, max_requests=settings.rate_limit_per_minute)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(router)
