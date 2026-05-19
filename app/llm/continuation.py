"""Token continuation — relance automatique si la réponse LLM est tronquée.

Quand le LLM atteint max_tokens, la réponse est coupée net.
Ce module détecte la troncature et relance pour obtenir la suite.
"""

from __future__ import annotations

from app.llm.base import LLMProvider

# Caractères terminaux : si la réponse finit par un de ceux-ci, elle est probablement complète
_TERMINAL_CHARS = frozenset(".!?}\"])\n»")

# Longueur minimale pour considérer une troncature possible
_MIN_LENGTH_FOR_TRUNCATION = 200


def _looks_truncated(text: str) -> bool:
    """Heuristique : la réponse semble-t-elle coupée ?"""
    stripped = text.rstrip()
    if not stripped:
        return False
    if len(stripped) < _MIN_LENGTH_FOR_TRUNCATION:
        return False
    return stripped[-1] not in _TERMINAL_CHARS


async def complete_with_continuation(
    llm: LLMProvider,
    messages: list[dict],
    *,
    max_rounds: int = 3,
    **kwargs,
) -> str:
    """Appelle llm.complete() avec relance automatique si la réponse est tronquée.

    Paramètres :
        llm: le provider LLM à utiliser
        messages: les messages de la conversation
        max_rounds: nombre maximum de relances (défaut 3)
        **kwargs: passés à llm.complete()

    Retourne la réponse complète concaténée.
    """
    full_response = ""
    current_messages = list(messages)

    for _ in range(max_rounds):
        chunk = await llm.complete(current_messages, **kwargs)
        full_response += chunk

        if not _looks_truncated(chunk):
            break

        # Relancer en demandant la suite
        current_messages = current_messages + [
            {"role": "assistant", "content": chunk},
            {"role": "user", "content": "Continue exactement où tu t'es arrêté."},
        ]

    return full_response
