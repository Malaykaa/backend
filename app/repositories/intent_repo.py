"""IntentRepository — CRUD + requêtes spécialisées pour UserIntent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_intent import UserIntent
from app.repositories.base import BaseRepository


class IntentRepository(BaseRepository[UserIntent]):
    def __init__(self, db: Session) -> None:
        super().__init__(UserIntent, db)

    def get_by_thread(self, thread_id: uuid.UUID) -> UserIntent | None:
        """Retourne l'intention active pour un thread (unique par thread_id)."""
        return self.db.execute(
            select(UserIntent).where(UserIntent.thread_id == thread_id)
        ).scalar_one_or_none()

    def get_latest_for_user(
        self, user_id: uuid.UUID, limit: int = 10
    ) -> list[UserIntent]:
        """Retourne les N dernières intentions extraites pour un utilisateur,
        triées par date d'extraction décroissante."""
        return list(
            self.db.execute(
                select(UserIntent)
                .where(UserIntent.user_id == user_id)
                .order_by(UserIntent.extracted_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

    def upsert(
        self,
        *,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        goal_id: uuid.UUID | None,
        intent_summary: str,
        intent_type: str | None,
        domain: str | None,
        keywords: list[str] | None,
        location: str | None,
        level: str | None,
        duration: str | None,
        raw_structured: dict | None,
        message_count: int,
    ) -> UserIntent:
        """Crée ou met à jour l'intention pour un thread.

        Utilise thread_id comme clé d'unicité :
        - première extraction → INSERT
        - re-extraction → UPDATE + incrémente version
        """
        existing = self.get_by_thread(thread_id)

        if existing:
            existing.intent_summary = intent_summary
            existing.intent_type = intent_type
            existing.domain = domain
            existing.keywords = keywords or []
            existing.location = location
            existing.level = level
            existing.duration = duration
            existing.raw_structured = raw_structured
            existing.message_count_at_extraction = message_count
            existing.version += 1
            existing.extracted_at = datetime.now(timezone.utc)
            self.db.flush()
            return existing

        intent = UserIntent(
            user_id=user_id,
            thread_id=thread_id,
            goal_id=goal_id,
            intent_summary=intent_summary,
            intent_type=intent_type,
            domain=domain,
            keywords=keywords or [],
            location=location,
            level=level,
            duration=duration,
            raw_structured=raw_structured,
            message_count_at_extraction=message_count,
            version=1,
        )
        self.db.add(intent)
        self.db.flush()
        return intent
