"""Abstraction LLM — Protocol que tout provider doit implémenter."""

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Interface commune pour tous les fournisseurs LLM."""

    async def complete(self, messages: list[dict], **kwargs) -> str:
        """Appel synchrone (retourne la réponse complète)."""
        ...

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        """Appel en streaming (yield des chunks de texte)."""
        ...
