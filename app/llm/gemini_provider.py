"""Google Gemini LLM provider — implémente LLMProvider avec google-genai."""

import asyncio
import logging
import re
from typing import AsyncIterator

from google import genai
from google.genai import types

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_RETRY_DELAY_RE = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


class GeminiProvider:
    """Appels LLM via l'API Google Gemini (SDK google-genai)."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model_name = settings.gemini_model

    # ── Conversion messages OpenAI → Gemini ──────────────

    @staticmethod
    def _prepare(
        messages: list[dict],
    ) -> tuple[str | None, list[types.Content]]:
        """Extrait le system instruction et convertit au format Gemini.

        OpenAI: role=system/user/assistant, content=str
        Gemini: role=user/model, parts=[Part], system via config
        Gemini exige une alternance stricte user/model → fusionne les rôles consécutifs.
        """
        system: str | None = None
        contents: list[types.Content] = []

        for m in messages:
            role = m.get("role", "user")
            text = m.get("content", "")
            if role == "system":
                system = text if system is None else f"{system}\n\n{text}"
            else:
                gemini_role = "model" if role == "assistant" else "user"
                if contents and contents[-1].role == gemini_role:
                    contents[-1].parts.append(types.Part(text=text))
                else:
                    contents.append(
                        types.Content(
                            role=gemini_role,
                            parts=[types.Part(text=text)],
                        )
                    )

        return system, contents

    # ── LLMProvider protocol ─────────────────────────────

    async def complete(self, messages: list[dict], **kwargs) -> str:
        system, contents = self._prepare(messages)
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=kwargs.get("temperature", 0.7),
            max_output_tokens=kwargs.get("max_tokens", 4096),
        )
        model = kwargs.get("model", self.model_name)

        for attempt in range(2):
            try:
                response = await self.client.aio.models.generate_content(
                    model=model, contents=contents, config=config,
                )
                return response.text or ""
            except Exception as exc:
                if attempt == 0 and "429" in str(exc):
                    delay = self._extract_retry_delay(str(exc))
                    logger.warning("Gemini 429 — retry in %.0fs", delay)
                    await asyncio.sleep(delay)
                    continue
                raise

        return ""  # unreachable, satisfies type checker

    @staticmethod
    def _extract_retry_delay(error_msg: str) -> float:
        """Extrait le délai de retry depuis le message d'erreur Gemini 429."""
        m = _RETRY_DELAY_RE.search(error_msg)
        if m:
            return min(float(m.group(1)), 60.0)  # cap à 60s
        return 10.0  # fallback raisonnable

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        system, contents = self._prepare(messages)
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=kwargs.get("temperature", 0.7),
            max_output_tokens=kwargs.get("max_tokens", 4096),
        )
        model = kwargs.get("model", self.model_name)

        for attempt in range(2):
            try:
                async for chunk in self.client.aio.models.generate_content_stream(
                    model=model, contents=contents, config=config,
                ):
                    if chunk.text:
                        yield chunk.text
                return  # stream terminé normalement
            except Exception as exc:
                if attempt == 0 and "429" in str(exc):
                    delay = self._extract_retry_delay(str(exc))
                    logger.warning("Gemini stream 429 — retry in %.0fs", delay)
                    await asyncio.sleep(delay)
                    continue
                raise
