"""Deterministic overlap classification for driving route alternatives.

Per Part 7.2: two routes that share most of their path must not be counted
as fully independent. Google's Routes API does not return a ready-made
"shared segment" figure, so this module derives a defensible, documented
overlap estimate from the road/street names mentioned in each route's turn-
by-turn `navigationInstruction` text (the only per-step identifying data the
`RouteResult` model carries) — a Jaccard-style overlap of the two routes'
road-name sets, weighted towards the higher-distance-share roads.

This is a deliberate, disclosed approximation (documented in
docs/transport-and-driving-models.md), not a geometric polyline
intersection — Part 14 forbids ML for this, and full polyline geometry
analysis was judged out of proportion to the value it would add here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.adapters.routing.base import RouteResult

OverlapClass = Literal["independent", "partially_independent", "substantially_overlapping", "not_practical"]

INDEPENDENT_MAX_OVERLAP = 0.30
PARTIALLY_INDEPENDENT_MAX_OVERLAP = 0.70
NOT_PRACTICAL_PENALTY_MINUTES = 15.0

_ROAD_SUFFIXES = (
    "Road|Ave|Avenue|Expressway|Highway|St|Street|Rd|Dr|Drive|Way|Lane|Ln|Blvd|Boulevard|Link"
    "|PIE|CTE|AYE|ECP|BKE|KJE|KPE|MCE|SLE|TPE"
)
_ROAD_TOKEN_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z']*(?:\s+[A-Z][a-zA-Z']*){0,3}(?:\s+(?:" + _ROAD_SUFFIXES + r"))\b)"
)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def extract_road_tokens(route: RouteResult) -> set[str]:
    """Best-effort extraction of named roads mentioned in a route's steps."""
    tokens: set[str] = set()
    for step in route.route_steps:
        cleaned = _strip_html(step.instruction)
        for match in _ROAD_TOKEN_PATTERN.finditer(cleaned):
            tokens.add(match.group(1).strip().upper())
    return tokens


@dataclass(frozen=True)
class OverlapResult:
    classification: OverlapClass
    overlap_ratio: float
    duration_penalty_minutes: float
    shared_roads: list[str]
    primary_roads: list[str]
    alternative_roads: list[str]
    evidence_note: str


def classify_alternative(
    primary: RouteResult,
    alternative: RouteResult,
    independent_max: float = INDEPENDENT_MAX_OVERLAP,
    partially_independent_max: float = PARTIALLY_INDEPENDENT_MAX_OVERLAP,
    not_practical_penalty_minutes: float = NOT_PRACTICAL_PENALTY_MINUTES,
) -> OverlapResult:
    primary_roads = extract_road_tokens(primary)
    alt_roads = extract_road_tokens(alternative)
    duration_penalty = round(alternative.duration_minutes - primary.duration_minutes, 1)

    if primary_roads and alt_roads:
        shared = primary_roads & alt_roads
        union = primary_roads | alt_roads
        overlap_ratio = len(shared) / len(union) if union else 0.0
        note = "Overlap estimated from shared named roads/expressways in each route's turn-by-turn steps."
    else:
        # Fallback: no road names could be parsed from either route's steps
        # (e.g. a provider response without navigationInstruction text) —
        # use distance/duration similarity as a much weaker overlap proxy,
        # clearly flagged as such in the evidence note.
        shorter_distance = min(primary.distance_metres, alternative.distance_metres)
        longer_distance = max(primary.distance_metres, alternative.distance_metres)
        distance_ratio = shorter_distance / longer_distance if longer_distance > 0 else 0.0
        overlap_ratio = distance_ratio
        shared = set()
        note = (
            "No road names could be parsed from route steps — overlap is a weak proxy "
            "based on distance similarity only, not confirmed shared roads."
        )

    if duration_penalty > not_practical_penalty_minutes:
        classification: OverlapClass = "not_practical"
    elif overlap_ratio <= independent_max:
        classification = "independent"
    elif overlap_ratio <= partially_independent_max:
        classification = "partially_independent"
    else:
        classification = "substantially_overlapping"

    return OverlapResult(
        classification=classification,
        overlap_ratio=round(overlap_ratio, 2),
        duration_penalty_minutes=duration_penalty,
        shared_roads=sorted(shared),
        primary_roads=sorted(primary_roads),
        alternative_roads=sorted(alt_roads),
        evidence_note=note,
    )
