"""OpenAI LLM provider — implémente le Protocol LLMProvider avec openai>=1.0."""

from typing import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import get_settings


def _convert_to_openai_format(messages: list[dict]) -> list[dict]:
    """Convertit les messages du format Claude vers le format OpenAI.

    Le format interne utilise la structure Claude pour les images :
      {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}

    OpenAI attend :
      {"type": "image_url", "image_url": {"url": "data:<media_type>;base64,<data>"}}
    """
    converted = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            converted.append(m)
            continue
        openai_blocks = []
        for block in content:
            if block.get("type") == "image":
                src = block.get("source", {})
                media_type = src.get("media_type", "image/jpeg")
                data = src.get("data", "")
                openai_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })
            else:
                openai_blocks.append(block)
        converted.append({**m, "content": openai_blocks})
    return converted


class OpenAIProvider:
    """Appels LLM via l'API OpenAI (GPT-4o-mini par défaut)."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    async def complete(self, messages: list[dict], **kwargs) -> str:
        response = await self.client.chat.completions.create(
            model=kwargs.get("model", self.model),
            messages=_convert_to_openai_format(messages),
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        return response.choices[0].message.content or ""

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        response = await self.client.chat.completions.create(
            model=kwargs.get("model", self.model),
            messages=_convert_to_openai_format(messages),
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
            stream=True,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
