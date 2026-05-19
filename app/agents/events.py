"""Événements de progression pour le streaming SSE.

Utilisés par l'ExecutionEngine pour communiquer l'avancement au frontend.
En mode direct : chunk → done
En mode workflow : planning → (step_start → chunk* → step_complete)* → done
"""

from __future__ import annotations

import enum
import json

from pydantic import BaseModel

from app.agents.base import AgentResponse


class EventType(str, enum.Enum):
    """Types d'événements SSE envoyés au frontend."""

    planning = "planning"          # Le système planifie les étapes
    step_start = "step_start"      # Une étape commence
    chunk = "chunk"                # Un morceau de texte (streaming LLM)
    step_complete = "step_complete" # Une étape est terminée
    done = "done"                  # Tout est terminé


class ProgressEvent(BaseModel):
    """Événement de progression envoyé en SSE au frontend."""

    type: EventType
    content: str = ""              # Texte du chunk ou message de progression
    agent_id: str | None = None
    step_index: int | None = None
    total_steps: int | None = None
    agent_response: AgentResponse | None = None  # Rempli uniquement pour "done"

    def to_sse(self) -> str:
        """Sérialise en format SSE (Server-Sent Events)."""
        data = json.dumps(self.model_dump(exclude_none=True), ensure_ascii=False)
        if self.type in (EventType.step_start, EventType.step_complete, EventType.done):
            return f"event: {self.type.value}\ndata: {data}\n\n"
        return f"data: {data}\n\n"
