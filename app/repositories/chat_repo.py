"""Repository chat — accès aux threads et messages."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

from app.models.chat import ChatMessage, ChatThread, MessageRole, ThreadStatus
from app.repositories.base import BaseRepository


class ChatRepository(BaseRepository[ChatThread]):
    def __init__(self, db: Session) -> None:
        super().__init__(ChatThread, db)

    def create_thread(
        self,
        user_id: uuid.UUID,
        title: str | None = None,
        goal_id: uuid.UUID | None = None,
    ) -> ChatThread:
        return self.create(user_id=user_id, title=title, goal_id=goal_id)

    def add_message(
        self,
        thread_id: uuid.UUID,
        role: MessageRole,
        content: str,
        payload: dict | None = None,
    ) -> ChatMessage:
        # Verrouiller le thread pour éviter les race conditions sur sequence_number
        self.db.execute(
            select(ChatThread)
            .where(ChatThread.id == thread_id)
            .with_for_update()
        ).scalar_one()

        max_seq = self.db.execute(
            select(func.coalesce(func.max(ChatMessage.sequence_number), 0))
            .where(ChatMessage.thread_id == thread_id)
        ).scalar()
        seq = max_seq + 1

        msg = ChatMessage(
            thread_id=thread_id, role=role, content=content,
            payload=payload, sequence_number=seq,
        )
        self.db.add(msg)
        self.db.flush()

        # Incrémenter message_count sur le thread
        self.db.execute(
            update(ChatThread)
            .where(ChatThread.id == thread_id)
            .values(message_count=ChatThread.message_count + 1)
        )
        self.db.flush()
        return msg

    def get_message(self, message_id: uuid.UUID) -> ChatMessage | None:
        return self.db.get(ChatMessage, message_id)

    def get_active_messages(self, thread_id: uuid.UUID) -> list[ChatMessage]:
        """Retourne les messages actifs du thread en ordre chronologique."""
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id, ChatMessage.is_active.is_(True))
            .order_by(ChatMessage.created_at.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def soft_delete(self, message_id: uuid.UUID) -> None:
        """Marque un message comme inactif (soft delete)."""
        self.db.execute(
            update(ChatMessage)
            .where(ChatMessage.id == message_id)
            .values(is_active=False)
        )
        self.db.flush()

    def cascade_after(self, thread_id: uuid.UUID, sequence_number: int) -> int:
        """Soft-delete tous les messages du thread avec sequence_number > seuil.

        Retourne le nombre de messages désactivés.
        """
        result = self.db.execute(
            update(ChatMessage)
            .where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.sequence_number > sequence_number,
                ChatMessage.is_active.is_(True),
            )
            .values(is_active=False)
        )
        self.db.flush()
        return result.rowcount

    def get_messages_after(self, thread_id: uuid.UUID, after_seq: int) -> list[ChatMessage]:
        """Retourne les messages actifs avec sequence_number > after_seq."""
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.is_active.is_(True),
                ChatMessage.sequence_number > after_seq,
            )
            .order_by(ChatMessage.sequence_number.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_messages_in_range(
        self, thread_id: uuid.UUID, start_seq: int, end_seq: int,
    ) -> list[ChatMessage]:
        """Retourne les messages actifs entre start_seq et end_seq (inclus)."""
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.is_active.is_(True),
                ChatMessage.sequence_number >= start_seq,
                ChatMessage.sequence_number <= end_seq,
            )
            .order_by(ChatMessage.sequence_number.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_thread_with_messages(self, thread_id: uuid.UUID) -> ChatThread | None:
        stmt = (
            select(ChatThread)
            .options(joinedload(ChatThread.messages), joinedload(ChatThread.goal))
            .where(ChatThread.id == thread_id)
        )
        return self.db.execute(stmt).unique().scalar_one_or_none()

    def get_thread_for_goal(self, goal_id: uuid.UUID, user_id: uuid.UUID) -> ChatThread | None:
        """Retourne le thread lié à un goal, avec ses messages chargés (aperçu)."""
        stmt = (
            select(ChatThread)
            .options(joinedload(ChatThread.messages))
            .where(ChatThread.goal_id == goal_id, ChatThread.user_id == user_id)
            .limit(1)
        )
        return self.db.execute(stmt).unique().scalar_one_or_none()

    def get_user_threads(self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[ChatThread]:
        stmt = (
            select(ChatThread)
            .where(ChatThread.user_id == user_id)
            .order_by(ChatThread.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_history(self, thread_id: uuid.UUID, limit: int = 50) -> list[dict]:
        """Retourne les N derniers messages actifs du thread pour le contexte LLM.

        Utilise une sous-requête DESC pour prendre les plus récents,
        puis retourne en ordre chronologique ASC.
        """
        subq = (
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id, ChatMessage.is_active.is_(True))
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
            .subquery()
        )
        stmt = select(ChatMessage).join(subq, ChatMessage.id == subq.c.id).order_by(ChatMessage.created_at.asc())
        messages = self.db.execute(stmt).scalars().all()
        return [{"role": m.role.value, "content": m.content} for m in messages]
