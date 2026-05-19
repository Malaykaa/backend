"""Repository objectifs — accès aux goals."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.goal import Goal, GoalType
from app.repositories.base import BaseRepository


class GoalRepository(BaseRepository[Goal]):
    def __init__(self, db: Session) -> None:
        super().__init__(Goal, db)

    def create_goal(
        self,
        user_id: uuid.UUID,
        goal_type: GoalType,
        context_data: dict | None = None,
    ) -> Goal:
        return self.create(user_id=user_id, type=goal_type, context_data=context_data)

    def get_user_goals(self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[Goal]:
        stmt = (
            select(Goal)
            .where(Goal.user_id == user_id)
            .order_by(Goal.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_with_plan(self, goal_id: uuid.UUID) -> Goal | None:
        stmt = (
            select(Goal)
            .options(joinedload(Goal.plan))
            .where(Goal.id == goal_id)
        )
        return self.db.execute(stmt).unique().scalar_one_or_none()
