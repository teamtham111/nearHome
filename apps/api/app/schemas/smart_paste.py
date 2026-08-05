"""Strict server-side schema for Smart Paste structured extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Confidence = Literal["HIGH", "MEDIUM", "LOW", "NONE"]
CandidateStatus = Literal["AVAILABLE", "NOT_FOUND_IN_SOURCE_TEXT", "EXTRACTION_UNCERTAIN"]


class SmartPasteFieldSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | float | None
    raw_text: str | None
    source_snippet: str | None
    source_section: str | None
    model_confidence: Confidence
    status: CandidateStatus


class AgentClaimSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    text: str
    verified: bool


class SmartPasteExtractionSchema(BaseModel):
    """The response schema sent to Groq and validated again on the server."""

    model_config = ConfigDict(extra="forbid")

    asking_price: SmartPasteFieldSchema
    floor_area_sqm: SmartPasteFieldSchema
    address: SmartPasteFieldSchema
    # A compact subtype such as 4A may be the only type evidence; the API
    # derives flat_type from it after validation.
    flat_type: SmartPasteFieldSchema | None
    # These are nullable but required in the provider schema. Groq's strict JSON
    # schema implementation requires every declared property to appear in
    # `required`; a missing optional fact is represented as JSON null.
    listing_flat_subtype: SmartPasteFieldSchema | None
    flat_model: SmartPasteFieldSchema | None
    # Missing lease facts are represented as JSON null. These remain required
    # properties so Groq's strict schema accepts the complete object shape.
    remaining_lease_years: SmartPasteFieldSchema | None
    lease_commencement_year: SmartPasteFieldSchema | None
    property_category: Literal["HDB", "NON_HDB", "UNKNOWN"]
    extraction_warnings: list[str]
    agent_claims: list[AgentClaimSchema]
