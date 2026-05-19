"""Schémas Pydantic pour les opportunités."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.models.opportunity import OpportunityType, UserOpportunityStatus


class OpportunityResponse(BaseModel):
    id: uuid.UUID
    type: OpportunityType
    title: str
    description: str | None = None
    source_url: str | None = None
    deadline: date | None = None
    country: str | None = None
    domain: str | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class MatchedOpportunityResponse(BaseModel):
    """Opportunité avec score de matching pour l'utilisateur."""
    opportunity: OpportunityResponse
    score: float
    status: UserOpportunityStatus
