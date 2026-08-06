#!/usr/bin/env python3
"""Measure local enrichment jobs without allowing a production target.

Prepare representative local sessions first (PT-only, driving-only, both, and
three-listing both), then pass them as labelled --scenario values. Re-run the
same session to observe warm/cache behaviour. The script never prints provider
credentials or listing data.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _json_request(url: str, method: str = "GET") -> dict:
    request = Request(url, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - loopback host is enforced below
        return json.loads(response.read())


def _local_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("--api-url must be a local http URL; production targets are refused")
    return value.rstrip("/")


def _run_scenario(base_url: str, label: str, session_id: str, interval: float) -> dict[str, object]:
    started = time.perf_counter()
    response = _json_request(f"{base_url}/api/v1/sessions/{session_id}/enrichment/start", method="POST")
    status_url = response["status_url"]
    stage_started = time.perf_counter()
    stage_durations: dict[str, float] = {}
    last_stage: str | None = None
    while True:
        status = _json_request(f"{base_url}{status_url}")
        stage = str(status.get("progress_stage", "unknown"))
        if last_stage is not None and stage != last_stage:
            stage_durations[last_stage] = round((time.perf_counter() - stage_started) * 1000, 2)
            stage_started = time.perf_counter()
        last_stage = stage
        if status["status"] in {"completed", "failed", "cancelled"}:
            if last_stage is not None:
                stage_durations[last_stage] = round((time.perf_counter() - stage_started) * 1000, 2)
            return {
                "label": label,
                "session_id": session_id,
                "job_id": response["job_id"],
                "status": status["status"],
                "total_duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "stage_durations_ms": stage_durations,
            }
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local NearHome enrichment sessions")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--scenario",
        action="append",
        required=True,
        metavar="LABEL=SESSION_ID",
        help="Repeat for PT-only, driving-only, both, three-listing-both, cold, warm and cache-repeat sessions.",
    )
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=Path("benchmark-enrichment-results.json"))
    args = parser.parse_args()
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be positive")
    base_url = _local_base_url(args.api_url)
    scenarios = []
    for value in args.scenario:
        label, separator, session_id = value.partition("=")
        if not separator or not label or not session_id:
            parser.error("--scenario must have the form LABEL=SESSION_ID")
        scenarios.append((label, session_id))
    results = [_run_scenario(base_url, label, session_id, args.poll_interval) for label, session_id in scenarios]
    args.output.write_text(json.dumps({"api_url": base_url, "scenarios": results}, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "scenarios": results}, indent=2))
    return 0 if all(result["status"] == "completed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
