"""Routes tracking — progression des étapes d'un plan."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.plan import StepCompleteResponse
from app.services.plan_service import PlanService

router = APIRouter(prefix="/goals", tags=["tracking"])


@router.patch("/{goal_id}/steps/{step_id}/complete", response_model=StepCompleteResponse)
def complete_step(
    goal_id: uuid.UUID,
    step_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = PlanService(db)
    step = service.complete_step(
        goal_id=goal_id,
        step_id=step_id,
        user_id=current_user.id,
    )
    db.commit()
    return step
