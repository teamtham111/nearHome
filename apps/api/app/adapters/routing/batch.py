"""Bounded, deterministic execution for independent synchronous route calls."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class RouteCall[T]:
    key: str
    call: Callable[[], T]


@dataclass(frozen=True)
class RouteCallOutcome[T]:
    result: T | None
    error: Exception | None


def run_bounded_route_calls[T](
    calls: list[RouteCall[T]], *, max_workers: int | None = None
) -> list[RouteCallOutcome[T]]:
    """Run unique route calls with a small pool and retain input ordering.

    Duplicate keys share one provider request. Exceptions remain attached to
    their individual call so a failed candidate never cancels valid evidence.
    """
    if not calls:
        return []
    unique: dict[str, RouteCall[T]] = {}
    for call in calls:
        unique.setdefault(call.key, call)
    limit = max(1, max_workers if max_workers is not None else settings.route_request_concurrency)

    def invoke(call: RouteCall[T]) -> RouteCallOutcome[T]:
        try:
            return RouteCallOutcome(result=call.call(), error=None)
        except Exception as exc:  # individual provider errors are expected evidence
            return RouteCallOutcome(result=None, error=exc)

    with ThreadPoolExecutor(max_workers=min(limit, len(unique)), thread_name_prefix="nearhome-routes") as executor:
        futures = {
            key: executor.submit(copy_context().run, invoke, call)
            for key, call in unique.items()
        }
        outcomes = {key: future.result() for key, future in futures.items()}
    return [outcomes[call.key] for call in calls]
