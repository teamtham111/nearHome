"""Structured logging setup."""

import logging
import sys
import time
import uuid

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import settings

def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.log_level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def new_correlation_id() -> str:
    cid = str(uuid.uuid4())
    return cid


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Add a safe request ID and structured completion/error log to every request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        request_id = request.headers.get("X-Request-ID") or new_correlation_id()
        started = time.perf_counter()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception:
            get_logger(__name__).exception(
                "request_failed",
                error_category="unhandled_exception",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "requestId": request_id},
            )
        else:
            get_logger(__name__).info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        finally:
            structlog.contextvars.clear_contextvars()

        response.headers["X-Request-ID"] = request_id
        return response
