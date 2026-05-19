"""Routes objectifs — création et listing des goals."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.goal import GoalCreate, GoalResponse
from app.services.goal_service import GoalService

router = APIRouter(prefix="/goals", tags=["goals"])


@router.post("", response_model=GoalResponse, status_code=201)
def create_goal(
    body: GoalCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = GoalService(db)
    goal = service.create_goal(
        user_id=current_user.id,
        goal_type=body.type,
        context_data=body.context_data,
    )
    db.commit()
    return goal


@router.get("", response_model=list[GoalResponse])
def list_goals(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    service = GoalService(db)
    return service.get_goals(current_user.id, limit=limit, offset=offset)


@router.get("/{goal_id}", response_model=GoalResponse)
def get_goal(
    goal_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = GoalService(db)
    return service.get_goal(goal_id, current_user.id)


@router.get("/{goal_id}/diagnostic", response_model=list[str])
def get_diagnostic_questions(
    goal_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = GoalService(db)
    goal = service.get_goal(goal_id, current_user.id)
    return GoalService.get_diagnostic_questions(goal.type.value)
