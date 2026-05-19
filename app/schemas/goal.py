"""Schémas Pydantic pour les objectifs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.goal import GoalStatus, GoalType


class GoalCreate(BaseModel):
    type: GoalType
    context_data: dict | None = None


class GoalResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    type: GoalType
    status: GoalStatus
    context_data: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
