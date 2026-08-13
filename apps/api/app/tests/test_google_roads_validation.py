"""Synthetic adversarial tests for offline Google Roads corroboration logic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

MODULE = Path(__file__).resolve().parents[4] / "data_pipeline" / "validate_major_road_google_roads.py"
spec = importlib.util.spec_from_file_location("google_roads_validation", MODULE)
assert spec and spec.loader
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def _points(place_ids: list[str], *, latitude: float = 1.30, longitude: float = 103.80) -> list[dict]:
    return [
        {"placeId": place_id, "location": {"latitude": latitude, "longitude": longitude + index * 0.001}}
        for index, place_id in enumerate(place_ids)
    ]


def test_same_corridor_and_small_lateral_offset_is_high_confidence() -> None:
    result = audit.compare_google_evidence(
        _points(["a", "a", "b", "c"]), _points(["a", "b", "c"], latitude=1.3001), audit.AuditConfig()
    )
    assert result["classification"] == "HIGH_CONFIDENCE"


def test_parallel_wrong_corridor_is_likely_incorrect() -> None:
    result = audit.compare_google_evidence(
        _points(["sla-1", "sla-2", "sla-3"]),
        _points(["other-1", "other-2", "other-3"], latitude=1.305),
        audit.AuditConfig(),
    )
    assert result["classification"] == "LIKELY_INCORRECT"


def test_reversed_same_geometry_keeps_corridor_evidence_but_reveals_order_difference() -> None:
    result = audit.compare_google_evidence(_points(["a", "b", "c"]), _points(["c", "b", "a"]), audit.AuditConfig())
    assert result["classification"] in {"MEDIUM_CONFIDENCE", "HIGH_CONFIDENCE"}
    assert result["ordered_segment_agreement"] < 1.0


def test_insufficient_google_evidence_is_not_passed() -> None:
    result = audit.compare_google_evidence(_points(["a"]), _points(["a"]), audit.AuditConfig())
    assert result["classification"] == "UNVALIDATABLE"


def test_regular_sampling_is_not_limited_to_source_vertices() -> None:
    samples = audit.sample_lines([[(103.8, 1.3), (103.82, 1.3)]], 100)
    assert len(samples) > 10
    assert samples[0] == (1.3, 103.8)
    assert samples[-1] == (1.3, 103.82)


def test_403_trace_preserves_google_reason_without_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = "not-a-real-secret"
    response = httpx.Response(
        403,
        json={
            "error": {
                "code": 403,
                "status": "PERMISSION_DENIED",
                "message": f"Roads API is not enabled for {secret}",
                "details": [{"reason": "SERVICE_DISABLED"}],
            }
        },
    )
    monkeypatch.setattr(audit.httpx, "get", lambda *args, **kwargs: response)
    client = audit.GoogleRoadsClient(
        secret,
        audit.GoogleRoadsCache(tmp_path, audit.SCHEMA_VERSION),
        allow_google=True,
        max_requests=1,
    )

    with pytest.raises(audit.GoogleRoadsRequestError, match="403: PERMISSION_DENIED"):
        client.snap(
            [(1.3, 103.8), (1.3001, 103.8001)],
            audit.AuditConfig(),
            audit.RequestContext("TEST ROAD", "SLA", 1, 1),
        )

    trace = client.request_trace[0]
    assert trace["road"] == "TEST ROAD"
    assert trace["http_status"] == 403
    assert trace["google_error"]["status"] == "PERMISSION_DENIED"
    assert trace["google_error_details"] == [{"reason": "SERVICE_DISABLED"}]
    assert secret not in str(trace)
    assert "?" not in trace["endpoint"]


def test_chunking_retains_one_boundary_point_and_never_exceeds_limit() -> None:
    points = [(1.3, 103.8 + number * 0.00001) for number in range(201)]
    chunks = audit._chunks(points, 100)
    assert [len(chunk) for chunk in chunks] == [100, 100, 3]
    assert chunks[0][-1] == chunks[1][0]
    assert chunks[1][-1] == chunks[2][0]
