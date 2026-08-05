"""Deterministic demo Smart Paste adapter used only when DEMO_MODE is enabled."""

from __future__ import annotations

import re
from typing import Any

from app.adapters.base import LLMExtractionResult
from app.core.config import settings
from app.services.smart_paste.flat_attributes import normalize_flat_type


class MockGroqAdapter:
    model_name = settings.groq_model

    def extract(self, cleaned_text: str) -> LLMExtractionResult:
        candidates: dict[str, list[dict[str, Any]]] = {}
        text = cleaned_text

        price = re.search(r"(?:asking(?:\s+price)?|price)\s*[:：]?\s*(?:S\$|\$)?\s*([\d,]+)", text, re.I)
        if price:
            candidates["asking_price"] = [
                self._candidate(float(price.group(1).replace(",", "")), price.group(0), "price", text)
            ]

        sqm = re.search(r"([\d.]+)\s*(?:sqm|m2|m²)", text, re.I)
        sqft = re.search(r"([\d.]+)\s*(?:sq\.?\s*ft|sqft)", text, re.I)
        if sqm:
            candidates["floor_area_sqm"] = [self._candidate(float(sqm.group(1)), sqm.group(0), "area", text)]
        elif sqft:
            value = round(float(sqft.group(1)) * 0.092903, 1)
            candidates["floor_area_sqm"] = [self._candidate(value, sqft.group(0), "area", text)]

        address = re.search(
            r"(?:(?:Blk|Block)\s*)?\d+[A-Za-z]?\s+[A-Za-z][\w\s.-]*?"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Way|Lane|Lorong|Lor)\s*\d*",
            text,
            re.I,
        )
        if address:
            candidates["address"] = [
                self._candidate(address.group(0).strip(), address.group(0).strip(), "address", text)
            ]

        flat_type = re.search(
            r"(?:flat\s+type|type)\s*[:：]?\s*((?:[2-5][\s-]*ROOM(?:S)?(?:\s*\([^)]*\))?)|"
            r"(?:[2-5][A-Z]{1,4})|EXECUTIVE)",
            text,
            re.I,
        )
        if not flat_type:
            flat_type = re.search(
                r"\b((?:[2-5][\s-]*ROOM(?:S)?(?:\s*\([^)]*\))?)|(?:[2-5][A-Z]{1,4}))\s+HDB\b",
                text,
                re.I,
            )
        if flat_type:
            attributes = normalize_flat_type(flat_type.group(1))
            if attributes.flat_type:
                candidates["flat_type"] = [self._candidate(attributes.flat_type, flat_type.group(0), "property", text)]
            if attributes.listing_flat_subtype:
                candidates["listing_flat_subtype"] = [
                    self._candidate(attributes.listing_flat_subtype, flat_type.group(0), "property", text)
                ]
            if attributes.flat_model:
                candidates["flat_model"] = [
                    {
                        **self._candidate(attributes.flat_model, flat_type.group(0), "property", text),
                        "extraction_method": "derived_from_subtype",
                    }
                ]

        # Only an explicitly-stated *remaining*/balance lease counts — a bare "99-year lease"
        # tenure statement is not the same thing and must not be copied in as remaining lease.
        lease = re.search(r"(?:remaining|balance)\s+lease\s*[:：~]?\s*(\d+(?:\.\d+)?)\s*years?", text, re.I)
        if lease:
            candidates["remaining_lease_years"] = [
                self._candidate(float(lease.group(1)), lease.group(0), "lease", text)
            ]

        heading = " ".join(text.splitlines()[:3]).strip().lower()
        explicit_non_hdb = re.search(
            r"^(?:condo(?:minium)?|apartment)\b|property\s+type\s*[:：]\s*(?:condo|apartment)",
            heading,
        )
        category = "NON_HDB" if explicit_non_hdb else "HDB"
        return LLMExtractionResult(
            candidates=candidates,
            extraction_warnings=["Demo extraction — verify all fields before confirming"],
            agent_claims=[],
            property_category=category,
            model_name=self.model_name,
            raw_response={"demo": True},
        )

    @staticmethod
    def _candidate(value: Any, raw_text: str, section: str, text: str) -> dict[str, Any]:
        return {
            "value": value,
            "raw_text": raw_text,
            "source_snippet": text[:200],
            "source_section": section,
            "model_confidence": "MEDIUM",
            "final_confidence": "MEDIUM",
            "verification_state": "UNVERIFIED",
            "status": "AVAILABLE",
            "extraction_method": "demo",
        }
