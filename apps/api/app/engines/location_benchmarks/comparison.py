"""Block-versus-block comparison maths for location benchmarks.

"Better than X%" = 100 × (comparison blocks with a strictly worse measurement)
÷ (valid comparison blocks), where the subject block is excluded from its own
comparison group, ties are never counted as worse, unavailable measurements
never join a group, and the percentage is floored to a whole number so it is
never overstated.

Suppression rule (documented in docs/location-benchmarks.md): a comparison is
withheld when the group has fewer than ``min_group_size`` valid comparison
blocks, or when fewer than ``min_coverage`` of the group's reference blocks
have a valid measurement.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any

RANKING_VERSION = "ranking-v1"
NATIONAL_MIN_GROUP_SIZE = 100
TOWN_MIN_GROUP_SIZE = 30
MIN_COVERAGE = 0.5
# Measurements are stored rounded by the engines; this guards float noise so
# genuinely identical measurements always compare as ties.
_PRECISION = 6


def badness(value: float | None, status: str, lower_is_better: bool) -> float | None:
    """Map a measurement onto one axis where a larger number is a worse result."""
    if status == "worst":
        return math.inf
    if status != "measured" or value is None:
        return None
    rounded = round(float(value), _PRECISION)
    return rounded if lower_is_better else -rounded


@dataclass(frozen=True)
class Cohort:
    """Sorted badness values of every valid block in one comparison group."""

    sorted_badness: tuple[float, ...]
    reference_block_count: int

    @classmethod
    def build(cls, badness_values: list[float], reference_block_count: int) -> Cohort:
        return cls(tuple(sorted(badness_values)), reference_block_count)

    @property
    def valid_count(self) -> int:
        return len(self.sorted_badness)

    @property
    def coverage(self) -> float:
        return self.valid_count / self.reference_block_count if self.reference_block_count else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "sorted_badness": [None if math.isinf(v) else v for v in self.sorted_badness],
            "worst_count": sum(1 for v in self.sorted_badness if math.isinf(v)),
            "reference_block_count": self.reference_block_count,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Cohort:
        finite = [float(v) for v in payload.get("sorted_badness", []) if v is not None]
        worst = [math.inf] * int(payload.get("worst_count", 0))
        return cls(tuple(sorted(finite) + worst), int(payload.get("reference_block_count", 0)))


@dataclass(frozen=True)
class ComparisonResult:
    status: str  # "available" | "suppressed" | "unavailable"
    better_than_pct: int | None
    strictly_worse: int | None
    tied: int | None
    group_size: int
    reference_block_count: int
    coverage: float
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "better_than_pct": self.better_than_pct,
            "strictly_worse": self.strictly_worse,
            "tied": self.tied,
            "group_size": self.group_size,
            "reference_block_count": self.reference_block_count,
            "coverage": round(self.coverage, 4),
            "reason": self.reason,
        }


def compare(
    subject_badness: float | None,
    cohort: Cohort,
    *,
    subject_in_cohort: bool,
    min_group_size: int,
    min_coverage: float = MIN_COVERAGE,
) -> ComparisonResult:
    """Compare one block's measurement with its group, excluding itself."""
    group_size = cohort.valid_count - (1 if subject_in_cohort and subject_badness is not None else 0)
    if subject_badness is None:
        return ComparisonResult(
            "unavailable",
            None,
            None,
            None,
            max(group_size, 0),
            cohort.reference_block_count,
            cohort.coverage,
            "This block's measurement is unavailable.",
        )
    if group_size < min_group_size:
        return ComparisonResult(
            "suppressed",
            None,
            None,
            None,
            group_size,
            cohort.reference_block_count,
            cohort.coverage,
            f"Only {group_size} comparable blocks have valid data (minimum {min_group_size}).",
        )
    if cohort.coverage < min_coverage:
        return ComparisonResult(
            "suppressed",
            None,
            None,
            None,
            group_size,
            cohort.reference_block_count,
            cohort.coverage,
            f"Only {cohort.coverage:.0%} of reference blocks have valid data (minimum {min_coverage:.0%}).",
        )
    values = cohort.sorted_badness
    strictly_worse = len(values) - bisect_right(values, subject_badness)
    equal = bisect_right(values, subject_badness) - bisect_left(values, subject_badness)
    tied = equal - (1 if subject_in_cohort else 0)
    pct = math.floor(100 * strictly_worse / group_size)
    return ComparisonResult(
        "available", pct, strictly_worse, max(tied, 0), group_size, cohort.reference_block_count, cohort.coverage
    )
