"""Schémas Pydantic pour le chat — requêtes et réponses HTTP."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.agents.base import AgentResponse, Source, Step, Suggestion
from app.models.chat import MessageRole, ThreadStatus


# ── Requêtes ─────────────────────────────────────────────


class ThreadCreate(BaseModel):
    title: str | None = Field(None, max_length=300)
    goal_id: uuid.UUID | None = None


class ThreadUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class MessageEdit(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


# ── Réponses ─────────────────────────────────────────────


class MessageResponse(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    role: MessageRole
    content: str
    payload: dict | None = None
    sequence_number: int | None = None
    is_active: bool = True
    previous_content: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ThreadResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    goal_id: uuid.UUID | None = None
    title: str | None = None
    status: ThreadStatus
    created_at: datetime
    messages: list[MessageResponse] = []

    model_config = {"from_attributes": True}


class ThreadListResponse(BaseModel):
    """Thread sans les messages (pour le listing)."""

    id: uuid.UUID
    user_id: uuid.UUID
    goal_id: uuid.UUID | None = None
    title: str | None = None
    status: ThreadStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    message_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageWithAgent(BaseModel):
    """Réponse après envoi d'un message : le message assistant + la réponse structurée."""

    message: MessageResponse
    agent_response: AgentResponse
