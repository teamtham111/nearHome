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


def test_failed_call_is_retried_once_after_the_configured_pause(monkeypatch) -> None:
    import app.adapters.routing.batch as batch

    pauses: list[float] = []
    monkeypatch.setattr(batch.time, "sleep", pauses.append)
    attempts = {"flaky": 0, "bad": 0}

    def flaky() -> str:
        attempts["flaky"] += 1
        if attempts["flaky"] == 1:
            raise TimeoutError("blip")
        return "ok"

    def bad() -> str:
        attempts["bad"] += 1
        raise TimeoutError("down")

    outcomes = run_bounded_route_calls(
        [RouteCall(key="flaky", call=flaky), RouteCall(key="bad", call=bad), RouteCall(key="fine", call=lambda: "x")],
        retry_failed=True,
        retry_pause_seconds=3.0,
    )

    assert [o.result for o in outcomes] == ["ok", None, "x"]
    assert attempts == {"flaky": 2, "bad": 2}  # retried exactly once, not again
    assert pauses == [3.0]  # one pause for the whole retry batch


def test_definitive_no_route_is_not_retried_and_does_not_pause(monkeypatch) -> None:
    import app.adapters.routing.batch as batch
    from app.adapters.routing.base import RoutingUnavailableError

    pauses: list[float] = []
    monkeypatch.setattr(batch.time, "sleep", pauses.append)
    attempts = 0

    def no_route() -> str:
        nonlocal attempts
        attempts += 1
        raise RoutingUnavailableError("no route", "TEST")

    outcomes = run_bounded_route_calls(
        [RouteCall(key="n", call=no_route)], retry_failed=True, retry_pause_seconds=3.0
    )

    assert attempts == 1 and pauses == []
    assert isinstance(outcomes[0].error, RoutingUnavailableError)


def test_no_pause_without_failures_or_without_retry(monkeypatch) -> None:
    import app.adapters.routing.batch as batch

    pauses: list[float] = []
    monkeypatch.setattr(batch.time, "sleep", pauses.append)
    run_bounded_route_calls([RouteCall(key="a", call=lambda: 1)], retry_failed=True, retry_pause_seconds=3.0)
    run_bounded_route_calls(
        [RouteCall(key="b", call=lambda: (_ for _ in ()).throw(TimeoutError()))], retry_pause_seconds=3.0
    )
    assert pauses == []
