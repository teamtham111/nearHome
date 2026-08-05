"""Simple in-memory rate limiting for expensive endpoints."""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

# session_id or client IP -> list of timestamps
_buckets: dict[str, list[float]] = defaultdict(list)

RATE_LIMITED_PREFIXES = (
    "/api/v1/sessions/",
)
RATE_LIMITED_SUFFIXES = (
    "/smart-paste",
    "/enrichment/start",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if request.method == "POST" and self._should_limit(path):
            key = request.client.host if request.client else "unknown"
            now = time.time()
            window_start = now - self.window_seconds
            hits = [t for t in _buckets[key] if t >= window_start]
            if len(hits) >= self.max_requests:
                return Response(
                    content='{"detail":"Rate limit exceeded — try again shortly"}',
                    status_code=429,
                    media_type="application/json",
                )
            hits.append(now)
            _buckets[key] = hits

        return await call_next(request)

    @staticmethod
    def _should_limit(path: str) -> bool:
        return "/smart-paste" in path or path.endswith("/enrichment/start")
