"""Package LLM — factory pour obtenir le provider configuré."""

import logging

from app.core.config import get_settings
from app.llm.base import LLMProvider
from app.llm.mock_provider import MockProvider

logger = logging.getLogger(__name__)


def get_llm_provider() -> LLMProvider:
    """Retourne le LLM provider selon LLM_PROVIDER dans .env."""
    settings = get_settings()
    if settings.llm_provider == "openai":
        from app.llm.openai_provider import OpenAIProvider
        return OpenAIProvider()
    if settings.llm_provider == "gemini":
        from app.llm.gemini_provider import GeminiProvider
        return GeminiProvider()
    if settings.llm_provider == "claude":
        from app.llm.claude_provider import ClaudeProvider
        return ClaudeProvider()
    if settings.environment not in ("local", "dev"):
        logger.warning(
            "LLM_PROVIDER is 'mock' in '%s' environment — "
            "set LLM_PROVIDER=openai or gemini in .env for real responses",
            settings.environment,
        )
    return MockProvider()
