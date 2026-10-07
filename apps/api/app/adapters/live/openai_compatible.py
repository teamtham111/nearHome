"""Adapter for providers that implement OpenAI-compatible chat completions."""

from openai import OpenAI

from app.adapters.base import AdapterError
from app.adapters.live.groq import LiveGroqAdapter
from app.core.config import settings


class LiveOpenAICompatibleAdapter(LiveGroqAdapter):
    """Reuse NearHome's normalized Smart Paste extraction over a compatible API."""

    provider = "OPENAI_COMPATIBLE"

    def __init__(self) -> None:
        if not settings.effective_llm_api_key:
            raise AdapterError(
                "No live LLM API key is configured. Add LLM_API_KEY to the server environment.",
                error_code="configuration",
            )
        self.client = OpenAI(
            api_key=settings.effective_llm_api_key,
            base_url=settings.effective_llm_base_url,
            timeout=60.0,
        )
