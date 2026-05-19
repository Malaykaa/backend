"""Anthropic Claude LLM provider — implémente LLMProvider avec le SDK anthropic."""

from typing import AsyncIterator

from anthropic import AsyncAnthropic

from app.core.config import get_settings


class ClaudeProvider:
    """Appels LLM via l'API Anthropic Claude."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = AsyncAnthropic(api_key=settings.claude_api_key)
        self.model = settings.claude_model

    # ── Conversion messages OpenAI → Claude ──────────────

    @staticmethod
    def _prepare(messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Extrait le system prompt et convertit au format Claude.

        OpenAI : role=system/user/assistant
        Claude : system séparé, role=user/assistant uniquement
        Claude exige une alternance stricte user/assistant → fusionne les rôles consécutifs.
        """
        system_parts: list[str] = []
        claude_messages: list[dict] = []

        for m in messages:
            role = m.get("role", "user")
            text = m.get("content", "")
            if role == "system":
                system_parts.append(text)
            else:
                # Claude n'accepte que "user" et "assistant"
                claude_role = "assistant" if role == "assistant" else "user"
                if claude_messages and claude_messages[-1]["role"] == claude_role:
                    # Fusionner les messages consécutifs du même rôle
                    claude_messages[-1]["content"] += f"\n\n{text}"
                else:
                    claude_messages.append({"role": claude_role, "content": text})

        system = "\n\n".join(system_parts) if system_parts else None
        return system, claude_messages

    # ── LLMProvider protocol ─────────────────────────────

    async def complete(self, messages: list[dict], **kwargs) -> str:
        system, claude_messages = self._prepare(messages)
        response = await self.client.messages.create(
            model=kwargs.get("model", self.model),
            system=system or "",
            messages=claude_messages,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        return response.content[0].text if response.content else ""

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        system, claude_messages = self._prepare(messages)
        async with self.client.messages.stream(
            model=kwargs.get("model", self.model),
            system=system or "",
            messages=claude_messages,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
        ) as stream:
            async for text in stream.text_stream:
                yield text
