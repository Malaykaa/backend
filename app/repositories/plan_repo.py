"""Repository plans — accès aux plans et étapes."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.plan import Plan, PlanStep, StepStatus
from app.repositories.base import BaseRepository


class PlanRepository(BaseRepository[Plan]):
    def __init__(self, db: Session) -> None:
        super().__init__(Plan, db)

    def create_plan(
        self,
        goal_id: uuid.UUID,
        summary: str,
        explanation: str,
        sources: dict | None = None,
        suggestions: dict | None = None,
    ) -> Plan:
        return self.create(
            goal_id=goal_id,
            summary=summary,
            explanation=explanation,
            sources=sources,
            suggestions=suggestions,
        )

    def add_step(
        self,
        plan_id: uuid.UUID,
        label: str,
        description: str,
        order: int,
    ) -> PlanStep:
        step = PlanStep(plan_id=plan_id, label=label, description=description, order=order)
        self.db.add(step)
        self.db.flush()
        return step

    def get_with_steps(self, plan_id: uuid.UUID) -> Plan | None:
        stmt = (
            select(Plan)
            .options(joinedload(Plan.steps))
            .where(Plan.id == plan_id)
        )
        return self.db.execute(stmt).unique().scalar_one_or_none()

    def get_by_goal_id(self, goal_id: uuid.UUID) -> Plan | None:
        stmt = (
            select(Plan)
            .options(joinedload(Plan.steps))
            .where(Plan.goal_id == goal_id)
        )
        return self.db.execute(stmt).unique().scalar_one_or_none()

    def get_step(self, step_id: uuid.UUID) -> PlanStep | None:
        return self.db.get(PlanStep, step_id)

    def complete_step(self, step: PlanStep) -> PlanStep:
        step.status = StepStatus.done
        self.db.flush()
        return step
