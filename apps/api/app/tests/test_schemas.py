"""API schema regression tests."""

import pytest
from pydantic import ValidationError

from app.schemas.comparison import BuyerProfileInput


def _profile(priorities: list[str]) -> dict:
    return {
        "max_budget": 700_000,
        "priorities": [{"priority_type": priority} for priority in priorities],
        "main_transport_mode": "MAINLY_PUBLIC_TRANSPORT",
    }


def test_profile_accepts_one_to_three_ordered_priorities() -> None:
    profile = BuyerProfileInput.model_validate(
        _profile(["PUBLIC_TRANSPORT", "SPACE", "LEASE"])
    )
    assert [p.priority_type.value for p in profile.priorities] == [
        "PUBLIC_TRANSPORT",
        "SPACE",
        "LEASE",
    ]


def test_profile_rejects_duplicate_priorities() -> None:
    with pytest.raises(ValidationError, match="cannot be selected more than once"):
        BuyerProfileInput.model_validate(_profile(["SPACE", "SPACE"]))


def test_profile_requires_at_least_one_priority() -> None:
    with pytest.raises(ValidationError):
        BuyerProfileInput.model_validate(_profile([]))


def test_profile_accepts_and_normalizes_multiple_named_schools() -> None:
    profile = BuyerProfileInput.model_validate(
        {
            **_profile(["SCHOOLS"]),
            "named_schools": [" Raffles Institution ", "Nanyang Primary School", "raffles institution"],
        }
    )

    assert profile.named_schools == ["Raffles Institution", "Nanyang Primary School"]
    assert profile.named_school == "Raffles Institution"


def test_profile_keeps_legacy_single_named_school_input_compatible() -> None:
    profile = BuyerProfileInput.model_validate(
        {**_profile(["SCHOOLS"]), "named_school": "Raffles Institution"}
    )

    assert profile.named_schools == ["Raffles Institution"]
