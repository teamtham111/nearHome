"""Smart Paste orchestration service."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.adapters.base import AdapterError
from app.adapters.factory import get_llm_adapter
from app.core.config import settings
from app.core.logging import get_logger
from app.core.utils import content_hash
from app.models.orm import ExtractionAttemptORM, ListingInputORM
from app.repositories.mappers import listing_input_from_orm
from app.services.smart_paste.preparation import prepare_paste_text
from app.services.smart_paste.retrieval import retrieve_listing_content
from app.services.smart_paste.validation import validate_extraction_candidates

logger = get_logger(__name__)


class SmartPasteService:
    MIN_LENGTH = 30
    MAX_LENGTH = 100_000

    def __init__(self, db: Session) -> None:
        self.db = db

    def extract(
        self,
        session_id: UUID,
        text: str,
        source_label: str | None = None,
        source_url: str | None = None,
        source_type: str = "text",
    ):
        original_text = text.strip() if text else ""
        if source_type == "url":
            if not source_url or not source_url.strip():
                raise ValueError("A listing URL is required")
            if settings.app_env == "development":
                logger.info("SMART_PASTE_INPUT_RECEIVED", source_type="url")
            retrieved = retrieve_listing_content(source_url)
            source_url = retrieved.source_url
            original_text = source_url
            text = retrieved.cleaned_text
        else:
            if not text or not text.strip():
                raise ValueError("Paste content cannot be empty")
            if len(text) > self.MAX_LENGTH:
                raise ValueError(f"Paste exceeds maximum length of {self.MAX_LENGTH}")
            if settings.app_env == "development":
                logger.info("SMART_PASTE_INPUT_RECEIVED", source_type="text", character_count=len(text))

        cleaned, prep_warnings = prepare_paste_text(text)
        if len(cleaned) < self.MIN_LENGTH:
            raise ValueError(f"Paste must be at least {self.MIN_LENGTH} characters after cleaning")

        extraction = ExtractionAttemptORM(
            session_id=session_id,
            original_text=original_text,
            cleaned_text=cleaned,
            source_label=source_label,
            source_url=source_url,
            character_count=len(original_text),
            content_hash=content_hash(original_text),
            pipeline_version=settings.smart_paste_pipeline_version,
            model_name=settings.groq_model,
            prompt_version=settings.smart_paste_prompt_version,
            schema_version=settings.smart_paste_schema_version,
            status="completed",
        )
        self.db.add(extraction)
        self.db.flush()

        try:
            llm_result = get_llm_adapter().extract(cleaned)
        except AdapterError as exc:
            logger.warning(
                "smart_paste_provider_error",
                error_code=exc.error_code,
                provider_status=exc.provider_status,
                provider_message=(exc.provider_message if settings.app_env == "development" else None),
            )
            raise

        candidates, val_warnings = validate_extraction_candidates(llm_result.candidates)
        all_warnings = prep_warnings + llm_result.extraction_warnings + val_warnings

        candidates_json = {
            field: [
                {
                    "value": c.value,
                    "raw_text": c.raw_text,
                    "source_snippet": c.source_snippet,
                    "source_section": c.source_section,
                    "extraction_method": c.extraction_method,
                    "model_confidence": c.model_confidence,
                    "final_confidence": c.final_confidence.value,
                    "verification_state": c.verification_state.value,
                    "status": c.status.value,
                    "conflicting_candidates": c.conflicting_candidates,
                }
                for c in cands
            ]
            for field, cands in candidates.items()
        }

        listing_input = ListingInputORM(
            session_id=session_id,
            extraction_id=extraction.id,
            raw_text=original_text,
            cleaned_text=cleaned,
            candidates_json=candidates_json,
            extraction_warnings=all_warnings,
            agent_claims=llm_result.agent_claims,
            source_label=source_label,
            source_url=source_url,
            property_category=llm_result.property_category,
            input_method="smart_paste",
            pipeline_version=settings.smart_paste_pipeline_version,
            model_name=llm_result.model_name,
            prompt_version=settings.smart_paste_prompt_version,
            schema_version=settings.smart_paste_schema_version,
        )
        self.db.add(listing_input)
        self.db.commit()
        self.db.refresh(listing_input)

        return listing_input_from_orm(listing_input), False

    def get_listing_input(self, listing_input_id: UUID):
        row = self.db.query(ListingInputORM).filter(ListingInputORM.id == listing_input_id).first()
        return listing_input_from_orm(row) if row else None
