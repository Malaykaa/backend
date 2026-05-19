"""Routes opportunités — liste matchée avec scores."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.opportunity import MatchedOpportunityResponse, OpportunityResponse
from app.services.opportunity_service import OpportunityService

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("", response_model=list[MatchedOpportunityResponse])
def list_matched_opportunities(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = OpportunityService(db)
    return service.match_for_user(
        user_id=current_user.id,
        profile=current_user.profile,
    )


@router.post("/{opportunity_id}/view", response_model=dict)
def mark_opportunity_viewed(
    opportunity_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = OpportunityService(db)
    service.repo.mark_viewed(current_user.id, opportunity_id)
    db.commit()
    return {"detail": "Opportunité marquée comme vue."}
