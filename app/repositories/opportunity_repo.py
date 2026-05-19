"""Repository opportunités — accès aux opportunités et matchings."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.opportunity import Opportunity, UserOpportunity, UserOpportunityStatus
from app.repositories.base import BaseRepository


class OpportunityRepository(BaseRepository[Opportunity]):
    def __init__(self, db: Session) -> None:
        super().__init__(Opportunity, db)

    def get_active(self) -> list[Opportunity]:
        stmt = (
            select(Opportunity)
            .where(Opportunity.is_active.is_(True))
            .order_by(Opportunity.created_at.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_user_matches(self, user_id: uuid.UUID) -> list[UserOpportunity]:
        stmt = (
            select(UserOpportunity)
            .where(UserOpportunity.user_id == user_id)
            .order_by(UserOpportunity.score.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def upsert_match(
        self,
        user_id: uuid.UUID,
        opportunity_id: uuid.UUID,
        score: float,
    ) -> UserOpportunity:
        stmt = (
            select(UserOpportunity)
            .where(
                UserOpportunity.user_id == user_id,
                UserOpportunity.opportunity_id == opportunity_id,
            )
        )
        existing = self.db.execute(stmt).scalar_one_or_none()

        if existing:
            existing.score = score
            self.db.flush()
            return existing

        match = UserOpportunity(
            user_id=user_id,
            opportunity_id=opportunity_id,
            score=score,
        )
        self.db.add(match)
        self.db.flush()
        return match

    def mark_viewed(self, user_id: uuid.UUID, opportunity_id: uuid.UUID) -> UserOpportunity | None:
        stmt = (
            select(UserOpportunity)
            .where(
                UserOpportunity.user_id == user_id,
                UserOpportunity.opportunity_id == opportunity_id,
            )
        )
        match = self.db.execute(stmt).scalar_one_or_none()
        if match and match.status == UserOpportunityStatus.new:
            match.status = UserOpportunityStatus.viewed
            self.db.flush()
        return match
