"""Gemini Smart Paste adapter using Google's native structured-output API."""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from app.adapters.base import AdapterError, LLMExtractionResult
from app.adapters.live.groq import (
    EXTRACTION_PROMPT,
    MAX_LLM_CONTENT_CHARS,
    LiveGroqAdapter,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
GEMINI_MAX_OUTPUT_TOKENS = 1800


class LiveGeminiAdapter(LiveGroqAdapter):
    """Use Gemini's native JSON Schema response mode for reliable extraction."""

    provider = "GEMINI"

    def __init__(self) -> None:
        if not settings.effective_llm_api_key:
            raise AdapterError(
                "No live LLM API key is configured. Add LLM_API_KEY to the server environment.",
                error_code="configuration",
            )
        self.client = genai.Client(api_key=settings.effective_llm_api_key)

    def extract(self, cleaned_text: str) -> LLMExtractionResult:
        if settings.app_env == "development":
            logger.info("SMART_PASTE_GEMINI_REQUEST_STARTED", character_count=len(cleaned_text))

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=cleaned_text[:MAX_LLM_CONTENT_CHARS],
                config=types.GenerateContentConfig(
                    system_instruction=EXTRACTION_PROMPT,
                    max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                    thinking_config=types.ThinkingConfig(thinking_level="LOW"),
                    response_mime_type="application/json",
                    response_json_schema=_gemini_json_schema(),
                ),
            )
        except Exception as exc:
            raise _gemini_provider_error(exc) from exc

        content = response.text
        if not content:
            raise AdapterError(
                "Gemini returned an empty extraction response.",
                error_code="empty_response",
            )
        return self._result_from_content(content)


def _gemini_json_schema() -> dict[str, Any]:
    """Remove JSON Schema keywords Gemini's API does not accept in this mode."""

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(child)
                for key, child in value.items()
                if key != "additionalProperties"
            }
        if isinstance(value, list):
            return [clean(child) for child in value]
        return value

    from app.schemas.smart_paste import SmartPasteExtractionSchema

    return clean(SmartPasteExtractionSchema.model_json_schema())


def _gemini_provider_error(exc: Exception) -> AdapterError:
    status = getattr(exc, "code", getattr(exc, "status_code", None))
    try:
        numeric_status = int(status)
    except (TypeError, ValueError):
        numeric_status = None
    provider_status = str(numeric_status) if numeric_status is not None else type(exc).__name__
    provider_message = getattr(exc, "message", None)
    if not isinstance(provider_message, str) or not provider_message.strip():
        provider_message = None
    elif settings.effective_llm_api_key:
        provider_message = provider_message.replace(settings.effective_llm_api_key, "[redacted]")
    if provider_message:
        provider_message = provider_message[:500]

    if numeric_status in {401, 403}:
        return AdapterError(
            "Gemini authentication failed. Check LLM_API_KEY.",
            provider_status=provider_status,
            error_code="authentication",
            provider_message=provider_message,
        )
    if numeric_status == 429:
        return AdapterError(
            "Gemini is temporarily rate limited. Try again later.",
            provider_status=provider_status,
            error_code="rate_limit",
            retryable=True,
            provider_message=provider_message,
        )
    if numeric_status in {500, 502, 503, 504}:
        return AdapterError(
            "Gemini is temporarily unavailable. Try again later.",
            provider_status=provider_status,
            error_code="temporarily_unavailable",
            retryable=True,
            provider_message=provider_message,
        )

    return AdapterError(
        "Gemini could not complete Smart Paste extraction.",
        provider_status=provider_status,
        error_code="provider_error",
        provider_message=provider_message,
    )
