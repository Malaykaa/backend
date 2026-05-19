"""Routes plans — génération et consultation des plans."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import extract_profile, get_current_user
from app.models.user import User
from app.schemas.plan import PlanGenerateRequest, PlanResponse
from app.services.plan_service import PlanService

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/goals/{goal_id}", response_model=PlanResponse, status_code=201)
async def generate_plan(
    goal_id: uuid.UUID,
    body: PlanGenerateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = PlanService(db)
    profile = extract_profile(current_user)

    plan = await service.generate_plan(
        goal_id=goal_id,
        user_id=current_user.id,
        answers=body.answers,
        profile=profile,
    )
    db.commit()
    return plan


@router.get("/goals/{goal_id}", response_model=PlanResponse)
def get_plan(
    goal_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = PlanService(db)
    return service.get_plan(goal_id, current_user.id)
