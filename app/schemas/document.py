"""Schémas Pydantic pour les documents (Actions Rapides)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.document import DocumentType


class DocumentGenerateRequest(BaseModel):
    type: DocumentType
    goal_id: uuid.UUID | None = None
    instructions: str | None = Field(None, max_length=2000)


class DocumentResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    goal_id: uuid.UUID | None = None
    type: DocumentType
    content: str
    export_url: str | None = None
    version: int
    created_at: datetime

    model_config = {"from_attributes": True}
