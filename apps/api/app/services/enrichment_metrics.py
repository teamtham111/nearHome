"""Request-scoped, privacy-safe enrichment performance metrics.

Metrics live in a context variable so repositories and routing adapters can add
measurements without being coupled to the worker or request handler. Outside an
enrichment run every helper is a cheap no-op.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class EnrichmentMetrics:
    job_id: str | None
    session_id: str
    stage_durations_ms: dict[str, float] = field(default_factory=dict)
    route_request_count: int = 0
    route_success_count: int = 0
    route_failure_count: int = 0
    route_retry_count: int = 0
    route_timeout_count: int = 0
    route_duration_ms: float = 0.0
    route_slowest_ms: float = 0.0
    route_cache_hits: int = 0
    route_cache_misses: int = 0
    database_read_count: int = 0
    database_read_duration_ms: float = 0.0
    database_write_count: int = 0
    database_write_duration_ms: float = 0.0
    database_commit_count: int = 0

    def summary(self) -> dict[str, object]:
        average_route_ms = self.route_duration_ms / self.route_request_count if self.route_request_count else 0.0
        return {
            "event": "enrichment_performance_summary",
            "job_id": self.job_id,
            "session_id": self.session_id,
            "stage_durations_ms": {name: round(value, 2) for name, value in self.stage_durations_ms.items()},
            "route_request_count": self.route_request_count,
            "route_success_count": self.route_success_count,
            "route_failure_count": self.route_failure_count,
            "route_retry_count": self.route_retry_count,
            "route_timeout_count": self.route_timeout_count,
            "route_duration_ms": round(self.route_duration_ms, 2),
            "route_average_ms": round(average_route_ms, 2),
            "route_slowest_ms": round(self.route_slowest_ms, 2),
            "route_cache_hits": self.route_cache_hits,
            "route_cache_misses": self.route_cache_misses,
            "database_read_count": self.database_read_count,
            "database_read_duration_ms": round(self.database_read_duration_ms, 2),
            "database_write_count": self.database_write_count,
            "database_write_duration_ms": round(self.database_write_duration_ms, 2),
            "database_commit_count": self.database_commit_count,
        }


_current_metrics: ContextVar[EnrichmentMetrics | None] = ContextVar("enrichment_metrics", default=None)


@contextmanager
def collect_enrichment_metrics(*, job_id: str | None, session_id: str) -> Iterator[EnrichmentMetrics]:
    metrics = EnrichmentMetrics(job_id=job_id, session_id=session_id)
    token = _current_metrics.set(metrics)
    started = perf_counter()
    try:
        yield metrics
    finally:
        metrics.stage_durations_ms["job_total"] = (perf_counter() - started) * 1000
        logger.info(**metrics.summary())
        _current_metrics.reset(token)


@contextmanager
def measure_stage(stage: str, *, listing_id: str | None = None) -> Iterator[None]:
    metrics = _current_metrics.get()
    started = perf_counter()
    try:
        yield
    finally:
        duration_ms = (perf_counter() - started) * 1000
        if metrics is not None:
            metrics.stage_durations_ms[stage] = metrics.stage_durations_ms.get(stage, 0.0) + duration_ms
            logger.info(
                "enrichment_stage_timing",
                job_id=metrics.job_id,
                session_id=metrics.session_id,
                listing_id=listing_id,
                stage=stage,
                duration_ms=round(duration_ms, 2),
            )


def record_route_request(duration_ms: float, *, success: bool, timeout: bool = False, retries: int = 0) -> None:
    metrics = _current_metrics.get()
    if metrics is None:
        return
    metrics.route_request_count += 1
    metrics.route_success_count += int(success)
    metrics.route_failure_count += int(not success)
    metrics.route_timeout_count += int(timeout)
    metrics.route_retry_count += retries
    metrics.route_duration_ms += duration_ms
    metrics.route_slowest_ms = max(metrics.route_slowest_ms, duration_ms)


def record_route_cache(*, hit: bool) -> None:
    metrics = _current_metrics.get()
    if metrics is not None:
        metrics.route_cache_hits += int(hit)
        metrics.route_cache_misses += int(not hit)


def record_database_read(duration_ms: float) -> None:
    metrics = _current_metrics.get()
    if metrics is not None:
        metrics.database_read_count += 1
        metrics.database_read_duration_ms += duration_ms


def record_database_write(duration_ms: float, *, committed: bool = False) -> None:
    metrics = _current_metrics.get()
    if metrics is not None:
        metrics.database_write_count += 1
        metrics.database_write_duration_ms += duration_ms
        metrics.database_commit_count += int(committed)
