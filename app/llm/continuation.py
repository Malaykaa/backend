"""Token continuation — relance automatique si la réponse LLM est tronquée.

Quand le LLM atteint max_tokens, la réponse est coupée net.
Ce module détecte la troncature et relance pour obtenir la suite.
"""

from __future__ import annotations

from app.llm.base import LLMProvider

# Caractères terminaux : si la réponse finit par un de ceux-ci, elle est probablement complète
_TERMINAL_CHARS = frozenset(".!?}\"])\n»")

# Marqueur de fin du bloc @@META@@ produit par les agents (cf. le format défini
# dans app/agents/base.py). Volontairement redéfini ici plutôt qu'importé :
# app.agents.base importe déjà ce module, l'importer en retour créerait un cycle.
#
# Une réponse qui se termine par ce marqueur est complète PAR CONSTRUCTION —
# le bloc est fermé. Sans ce test, elle était jugée tronquée parce que son
# dernier caractère ("@") n'appartient pas à _TERMINAL_CHARS, ce qui déclenchait
# deux relances LLM inutiles sur le cas le plus fréquent de l'application
# (toute réponse portant clarifications, steps, suggestions, offers ou metiers).
# Le texte de ces relances était ensuite intégralement jeté par _parse_meta_block,
# qui ne conserve que ce qui précède le premier @@META@@ : coût et latence
# triplés pour un résultat rigoureusement identique.
_META_END_MARKER = "@@END@@"

# Longueur minimale pour considérer une troncature possible
_MIN_LENGTH_FOR_TRUNCATION = 200


def _looks_truncated(text: str) -> bool:
    """Heuristique : la réponse semble-t-elle coupée ?"""
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.endswith(_META_END_MARKER):
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
