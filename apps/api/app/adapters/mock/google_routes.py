"""Mock Google Routes — demo route matrix with per-origin durations."""

from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.base import RouteMatrixElement, RouteMatrixResult

# Base durations in seconds per origin index (varies by listing)
_PT_BASE = [1860, 2100, 1740, 2280, 1920]
_DRIVING_BASE = [1200, 1380, 1080, 1500, 1260]


class MockGoogleRoutesAdapter:
    provider = "MOCK_GOOGLE_ROUTES"

    def route_matrix(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
        mode: str,
        departure_at: datetime,
    ) -> RouteMatrixResult:
        base_list = _PT_BASE if mode == "PUBLIC_TRANSPORT" else _DRIVING_BASE
        elements: list[RouteMatrixElement] = []
        for idx, _origin in enumerate(origins):
            duration = base_list[idx % len(base_list)]
            # Simulate partial failure for index 2 in PT mode (demo)
            if mode == "PUBLIC_TRANSPORT" and idx == 2 and len(origins) >= 3:
                elements.append(
                    RouteMatrixElement(
                        origin_index=idx,
                        duration_seconds=None,
                        status="UNAVAILABLE",
                        provider_status="ROUTE_NOT_FOUND",
                    )
                )
            else:
                elements.append(
                    RouteMatrixElement(
                        origin_index=idx,
                        duration_seconds=duration,
                        status="AVAILABLE",
                        provider_status="OK",
                    )
                )
        return RouteMatrixResult(
            elements=elements,
            provider=self.provider,
            retrieved_at=datetime.now(UTC),
            resolved_departure_at=departure_at,
        )
