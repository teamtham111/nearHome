"""Shared configuration for external-provider payload models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProviderPayload(BaseModel):
    """Validate fields NearHome consumes while tolerating provider additions."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def empty_to_none(value: object) -> object:
    """Treat provider empty-string sentinels as explicit missing values."""

    return None if value in (None, "") else value
