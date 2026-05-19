"""OpenAI LLM provider — implémente le Protocol LLMProvider avec openai>=1.0."""

from typing import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import get_settings


class OpenAIProvider:
    """Appels LLM via l'API OpenAI (GPT-4o-mini par défaut)."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    async def complete(self, messages: list[dict], **kwargs) -> str:
        response = await self.client.chat.completions.create(
            model=kwargs.get("model", self.model),
            messages=messages,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        return response.choices[0].message.content or ""

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        response = await self.client.chat.completions.create(
            model=kwargs.get("model", self.model),
            messages=messages,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
            stream=True,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
