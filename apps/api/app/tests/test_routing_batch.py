"""Safety properties for bounded, deterministic route candidate execution."""

from __future__ import annotations

import threading
import time

from app.adapters.routing.batch import RouteCall, run_bounded_route_calls


def test_route_calls_respect_the_worker_limit_and_preserve_input_order() -> None:
    active = maximum = 0
    lock = threading.Lock()

    def call(value: int) -> int:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return value

    outcomes = run_bounded_route_calls(
        [RouteCall(key=f"route-{value}", call=lambda value=value: call(value)) for value in range(6)],
        max_workers=2,
    )

    assert maximum == 2
    assert [outcome.result for outcome in outcomes] == list(range(6))
    assert all(outcome.error is None for outcome in outcomes)


def test_duplicate_keys_share_a_call_and_failures_stay_local() -> None:
    calls = 0

    def duplicate() -> str:
        nonlocal calls
        calls += 1
        return "shared"

    outcomes = run_bounded_route_calls(
        [
            RouteCall(key="same", call=duplicate),
            RouteCall(key="bad", call=lambda: (_ for _ in ()).throw(TimeoutError("timeout"))),
            RouteCall(key="same", call=duplicate),
        ],
        max_workers=2,
    )

    assert calls == 1
    assert outcomes[0].result == outcomes[2].result == "shared"
    assert outcomes[1].result is None
    assert isinstance(outcomes[1].error, TimeoutError)
