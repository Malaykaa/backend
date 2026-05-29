"""Service mémoire — compression des longues conversations pour contrôler les coûts tokens.

Deux variantes :
- MemoryService      : sync (APScheduler, MatchRunner, `def` routers)
- AsyncMemoryService : async (routers SSE `async def` — chat, action_adapter)
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.llm.base import LLMProvider
from app.repositories.chat_repo import AsyncChatRepository, ChatRepository

# Seuil : compresser quand il y a plus de 6 nouveaux messages depuis le dernier résumé
COMPRESSION_THRESHOLD = 6
# En-dessous de ce nombre de messages, pas de compression (retourne tout)
MIN_MESSAGES_FOR_COMPRESSION = 10


class MemoryService:
    def __init__(self, db: Session) -> None:
        self.repo = ChatRepository(db)

    def get_context(self, thread_id: uuid.UUID) -> dict:
        """Retourne le contexte pour le LLM : résumé + messages récents.

        - Si message_count < 10 → retourne tous les messages actifs
        - Sinon → retourne compressed_summary + messages après summary_up_to
        """
        thread = self.repo.get_by_id(thread_id)
        if not thread:
            return {"summary": None, "recent_messages": []}

        # Conversation courte : tout retourner
        if thread.message_count < MIN_MESSAGES_FOR_COMPRESSION:
            messages = self.repo.get_active_messages(thread_id)
            return {
                "summary": None,
                "recent_messages": [
                    {"role": m.role.value, "content": m.content} for m in messages
                ],
            }

        # Conversation longue : résumé + messages récents
        summary = thread.compressed_summary
        after_seq = thread.summary_up_to or 0
        recent = self.repo.get_messages_after(thread_id, after_seq)

        return {
            "summary": summary,
            "recent_messages": [
                {"role": m.role.value, "content": m.content} for m in recent
            ],
        }

    async def maybe_compress(
        self, thread_id: uuid.UUID, llm: LLMProvider,
    ) -> bool:
        """Compresse l'historique si le seuil est dépassé. Retourne True si compression faite."""
        thread = self.repo.get_by_id(thread_id)
        if not thread:
            return False

        summary_up_to = thread.summary_up_to or 0

        # Pas assez de messages pour compresser
        if thread.message_count < MIN_MESSAGES_FOR_COMPRESSION:
            return False

        new_since_summary = thread.message_count - summary_up_to
        if new_since_summary < COMPRESSION_THRESHOLD:
            return False

        # Récupérer les messages à compresser
        end_seq = summary_up_to + COMPRESSION_THRESHOLD
        messages_to_compress = self.repo.get_messages_in_range(
            thread_id, summary_up_to + 1, end_seq,
        )
        if not messages_to_compress:
            return False

        # Construire le prompt de compression
        conversation_text = "\n".join(
            f"{m.role.value}: {m.content}" for m in messages_to_compress
        )

        previous_summary = thread.compressed_summary or ""
        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "Tu es un assistant qui résume des conversations. "
                    "Résume en 2-3 phrases concises en français, en gardant les informations clés "
                    "(objectifs, décisions, contexte important)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Contexte précédent : {previous_summary}\n\n"
                    f"Nouvelle conversation à intégrer :\n{conversation_text}\n\n"
                    "Résume l'ensemble en 2-3 phrases."
                    if previous_summary
                    else f"Conversation à résumer :\n{conversation_text}\n\n"
                    "Résume en 2-3 phrases."
                ),
            },
        ]

        # Appeler le LLM pour générer le résumé
        summary = await llm.complete(prompt_messages)

        # Mettre à jour le thread
        thread.compressed_summary = summary
        thread.summary_up_to = end_seq
        self.repo.save(thread)

        return True


# ── Variante async (asyncpg) ────────────────────────────────────────────────


class AsyncMemoryService:
    """Variante async de MemoryService pour les routers SSE (asyncpg)."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = AsyncChatRepository(db)

    async def get_context(self, thread_id: uuid.UUID) -> dict:
        """Retourne le contexte LLM : résumé compressé + messages récents."""
        thread = await self.repo.get_by_id(thread_id)
        if not thread:
            return {"summary": None, "recent_messages": []}

        if thread.message_count < MIN_MESSAGES_FOR_COMPRESSION:
            messages = await self.repo.get_active_messages(thread_id)
            return {
                "summary": None,
                "recent_messages": [
                    {"role": m.role.value, "content": m.content} for m in messages
                ],
            }

        summary = thread.compressed_summary
        after_seq = thread.summary_up_to or 0
        recent = await self.repo.get_messages_after(thread_id, after_seq)
        return {
            "summary": summary,
            "recent_messages": [
                {"role": m.role.value, "content": m.content} for m in recent
            ],
        }

    async def maybe_compress(
        self, thread_id: uuid.UUID, llm: LLMProvider,
    ) -> bool:
        """Compresse l'historique si le seuil est dépassé. Retourne True si compression faite."""
        thread = await self.repo.get_by_id(thread_id)
        if not thread:
            return False

        summary_up_to = thread.summary_up_to or 0
        if thread.message_count < MIN_MESSAGES_FOR_COMPRESSION:
            return False

        new_since_summary = thread.message_count - summary_up_to
        if new_since_summary < COMPRESSION_THRESHOLD:
            return False

        end_seq = summary_up_to + COMPRESSION_THRESHOLD
        messages_to_compress = await self.repo.get_messages_in_range(
            thread_id, summary_up_to + 1, end_seq,
        )
        if not messages_to_compress:
            return False

        conversation_text = "\n".join(
            f"{m.role.value}: {m.content}" for m in messages_to_compress
        )
        previous_summary = thread.compressed_summary or ""
        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "Tu es un assistant qui résume des conversations. "
                    "Résume en 2-3 phrases concises en français, en gardant les informations clés "
                    "(objectifs, décisions, contexte important)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Contexte précédent : {previous_summary}\n\n"
                    f"Nouvelle conversation à intégrer :\n{conversation_text}\n\n"
                    "Résume l'ensemble en 2-3 phrases."
                    if previous_summary
                    else f"Conversation à résumer :\n{conversation_text}\n\n"
                    "Résume en 2-3 phrases."
                ),
            },
        ]

        summary = await llm.complete(prompt_messages)
        thread.compressed_summary = summary
        thread.summary_up_to = end_seq
        await self.repo.save(thread)
        return True
