"""School proximity comparison when schools_matter is enabled."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.adapters.reference_data import ReferenceDataStore, School, haversine_m
from app.core.config import settings
from app.domain.enums import ConfidenceLevel, DataStatus, Provenance, ScoreStatus


@dataclass
class NearbySchool:
    school_name: str
    level: str
    distance_km: float
    address: str


@dataclass
class SchoolsResult:
    nearby_schools: list[NearbySchool]
    named_school_distance_km: float | None
    named_school_distances_km: dict[str, float | None]
    matched_named_schools: dict[str, str | None]
    status: DataStatus
    confidence: ConfidenceLevel
    explanation: str
    provenance: Provenance
    schools_within_1km: int
    schools_within_2km: int
    nearest_school_distance_km: float | None
    score: float | None
    score_status: ScoreStatus
    missing_reasons: list[str]
    warnings: list[str]


class SchoolsEngine:
    SEARCH_RADIUS_KM = 2.0
    # These are widely used, unambiguous abbreviations. New aliases should only be
    # added when they are backed by the official MOE reference snapshot.
    SCHOOL_ALIASES = {
        "ri": "raffles institution",
        "rjc": "raffles institution",
        "hci": "hwa chong institution",
    }

    @staticmethod
    def _normalise_school_name(value: str, *, remove_suffix: bool = False) -> str:
        """Normalise display-only variation without silently broadening a match."""
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        value = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
        value = re.sub(r"\s+", " ", value)
        if remove_suffix:
            value = re.sub(r"\b(primary|secondary|junior|school|college|institution)\b", " ", value)
            value = re.sub(r"\s+", " ", value).strip()
        return value

    @classmethod
    def _match_named_school(cls, target_name: str, schools: list[School]) -> School | None:
        """Return an official school only when normalisation yields one candidate."""
        target = cls._normalise_school_name(target_name)
        if not target:
            return None
        target = cls.SCHOOL_ALIASES.get(target, target)
        exact = [school for school in schools if cls._normalise_school_name(school.school_name) == target]
        if len(exact) == 1:
            return exact[0]
        if exact:
            return None

        simplified_target = cls._normalise_school_name(target, remove_suffix=True)
        simplified = [
            school
            for school in schools
            if cls._normalise_school_name(school.school_name, remove_suffix=True) == simplified_target
        ]
        return simplified[0] if len(simplified) == 1 else None

    @classmethod
    def compute(
        cls,
        listing_id: UUID,
        latitude: float | None,
        longitude: float | None,
        named_school: str | None = None,
        named_schools: list[str] | None = None,
    ) -> SchoolsResult:
        if latitude is None or longitude is None:
            return SchoolsResult(
                nearby_schools=[],
                named_school_distance_km=None,
                named_school_distances_km={},
                matched_named_schools={},
                status=DataStatus.UNAVAILABLE,
                confidence=ConfidenceLevel.NONE,
                explanation="Coordinates unavailable — run geocoding first",
                provenance=Provenance.CALCULATED,
                schools_within_1km=0,
                schools_within_2km=0,
                nearest_school_distance_km=None,
                score=None,
                score_status=ScoreStatus.MISSING_INPUT,
                missing_reasons=["Coordinates unavailable — run geocoding first"],
                warnings=[],
            )

        all_schools = ReferenceDataStore.schools()
        raw = ReferenceDataStore.nearby_schools(latitude, longitude, cls.SEARCH_RADIUS_KM)
        deduplicated: list[tuple[School, float]] = []
        seen: set[tuple[str, float, float]] = set()
        for school, distance in raw:
            key = (school.school_name.strip().upper(), round(school.latitude, 6), round(school.longitude, 6))
            if key not in seen:
                seen.add(key)
                deduplicated.append((school, distance))
        raw = deduplicated
        within_1km = sum(1 for _school, distance in raw if distance <= 1.0)
        within_2km = sum(1 for _school, distance in raw if distance <= 2.0)
        nearby = [
            NearbySchool(
                school_name=s.school_name,
                level=s.level,
                distance_km=dist,
                address=s.address,
            )
            for s, dist in raw
        ]

        targets = named_schools or ([named_school] if named_school else [])
        named_distances: dict[str, float | None] = {}
        matched_named_schools: dict[str, str | None] = {}
        for target_name in targets:
            selected_name = target_name.strip()
            if not selected_name:
                continue
            matched_school = cls._match_named_school(selected_name, all_schools)
            matched_named_schools[selected_name] = matched_school.school_name if matched_school else None
            nearby_distance = next((distance for school, distance in raw if school == matched_school), None)
            named_distances[selected_name] = nearby_distance if nearby_distance is not None else (
                haversine_m(latitude, longitude, matched_school.latitude, matched_school.longitude) / 1000
                if matched_school
                else None
            )
        named_dist = next(iter(named_distances.values()), None)

        reference_available = bool(ReferenceDataStore.schools())
        score = min(100.0, min(within_1km, 5) * 15.0 + min(max(within_2km - within_1km, 0), 5) * 5.0)
        warnings = ["Distance does not guarantee admission, registration priority or eligibility."]
        for target_name, distance in named_distances.items():
            if distance is None:
                warnings.append(f"Named school not found in the reference snapshot: {target_name}")

        provenance = Provenance.OFFICIAL if not settings.demo_mode else Provenance.MOCK_DEMO_DATA
        return SchoolsResult(
            nearby_schools=nearby,
            named_school_distance_km=named_dist,
            named_school_distances_km=named_distances,
            matched_named_schools=matched_named_schools,
            status=DataStatus.AVAILABLE if reference_available else DataStatus.UNAVAILABLE,
            confidence=ConfidenceLevel.MEDIUM if len(nearby) >= 2 else ConfidenceLevel.LOW,
            explanation=f"{within_1km} schools within 1 km and {within_2km} within 2 km (MOE reference snapshot)",
            provenance=provenance,
            schools_within_1km=within_1km,
            schools_within_2km=within_2km,
            nearest_school_distance_km=raw[0][1] if raw else None,
            score=score if reference_available else None,
            score_status=ScoreStatus.CALCULATED if reference_available else ScoreStatus.UNAVAILABLE,
            missing_reasons=[] if reference_available else ["MOE school reference data unavailable"],
            warnings=warnings,
        )

    @classmethod
    def to_dict(cls, result: SchoolsResult) -> dict[str, Any]:
        return {
            "nearby_schools": [
                {
                    "school_name": s.school_name,
                    "level": s.level,
                    "distance_km": s.distance_km,
                    "address": s.address,
                }
                for s in result.nearby_schools
            ],
            "named_school_distance_km": result.named_school_distance_km,
            "named_school_distances_km": result.named_school_distances_km,
            "matched_named_schools": result.matched_named_schools,
            "schools_within_1km": result.schools_within_1km,
            "schools_within_2km": result.schools_within_2km,
            "nearest_school_distance_km": result.nearest_school_distance_km,
            "score": result.score,
            "score_status": result.score_status.value,
            "missing_reasons": result.missing_reasons,
            "warnings": result.warnings,
            "status": result.status.value,
            "confidence": result.confidence.value,
            "explanation": result.explanation,
        }
