"""Schémas Pydantic pour les plans et étapes."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.plan import StepStatus


class PlanStepResponse(BaseModel):
    id: uuid.UUID
    label: str
    description: str
    order: int
    status: StepStatus

    model_config = {"from_attributes": True}


class PlanResponse(BaseModel):
    id: uuid.UUID
    goal_id: uuid.UUID
    summary: str
    explanation: str
    sources: list | None = None
    suggestions: list | None = None
    version: int
    created_at: datetime
    steps: list[PlanStepResponse] = []

    model_config = {"from_attributes": True}


class PlanGenerateRequest(BaseModel):
    """Données de diagnostic pour générer le plan."""
    answers: dict = {}


class StepCompleteResponse(BaseModel):
    id: uuid.UUID
    status: StepStatus

    model_config = {"from_attributes": True}
