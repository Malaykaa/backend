"""Formatage SSE — unifie les transformations de ProgressEvent → flux SSE.

Trois variantes selon le canal :
- format_chat_event   : flux conversationnel /chat (events nommés, mapping frontend)
- format_smart_event  : flux /ai/actions/{preset}/smart/stream (livrable OU conversation)
- format_swarm_event  : flux /ai/actions/swarm/{action_type}/stream (livrable seul)

Toutes partagent la branche progression (planning / step_start / step_complete / chunk),
factorisée dans _progress_payload() pour éviter les divergences de mapping.
"""

from __future__ import annotations

import json

from app.agents.events import EventType, ProgressEvent
from app.services.message_formatter import format_sources, inject_markers

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(payload: dict, *, event_name: str | None = None) -> str:
    """Sérialise un payload en frame SSE (avec ou sans nom d'événement)."""
    data = json.dumps(payload, ensure_ascii=False)
    if event_name:
        return f"event: {event_name}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def _progress_payload(event: ProgressEvent, *, default_agent_id: str = "") -> dict | None:
    """Retourne le payload pour les événements de progression (commun aux 3 canaux).

    Renvoie None si l'event n'est pas un événement de progression.
    """
    if event.type == EventType.planning:
        return {"type": "generating"}
    if event.type == EventType.step_start:
        return {
            "type": "agent_start",
            "agentId": event.agent_id or default_agent_id,
            "label": event.content,
        }
    if event.type == EventType.step_complete:
        return {
            "type": "agent_done",
            "agentId": event.agent_id or default_agent_id,
        }
    if event.type == EventType.chunk:
        return {"type": "token", "token": event.content}
    return None


def format_chat_event(event: ProgressEvent) -> str:
    """Variante /chat : events nommés (event: foo\\ndata: ...) et done enrichi.

    Le frontend chat utilise des EventSource nommés pour router vers les handlers.
    """
    if event.type == EventType.planning:
        return _sse({"type": "generating"}, event_name="generating")

    if event.type == EventType.step_start:
        return _sse(
            {
                "type": "agent_start",
                "agentId": event.agent_id or "",
                "label": event.content,
            },
            event_name="agent_start",
        )

    if event.type == EventType.step_complete:
        return _sse(
            {"type": "agent_done", "agentId": event.agent_id or ""},
            event_name="agent_done",
        )

    if event.type == EventType.chunk:
        return _sse({"type": "token", "token": event.content})

    if event.type == EventType.done and event.agent_response:
        resp = event.agent_response
        sources = format_sources(resp.model_dump())
        done_payload: dict = {
            "type": "done",
            "sources": sources or [],
            "qualityScore": 85,
        }
        if resp.deliverables:
            done_payload["content"] = "\n\n".join(resp.deliverables)
            done_payload["isDeliverable"] = True
            done_payload["explanation"] = inject_markers(resp.explanation, resp.model_dump())
        else:
            done_payload["content"] = inject_markers(resp.explanation, resp.model_dump())
        return _sse(done_payload, event_name="done")

    return event.to_sse()


def format_smart_event(event: ProgressEvent) -> str:
    """Variante /ai/actions/.../smart : livrable OU réponse conversationnelle.

    Les events sont sans nom (data: only). Le 'done' devient 'message_done'
    en mode conversationnel pour distinguer du livrable côté frontend.
    """
    progress = _progress_payload(event, default_agent_id="document")
    if progress is not None:
        return _sse(progress)

    if event.type == EventType.done and event.agent_response:
        resp = event.agent_response
        if resp.deliverables:
            sources = format_sources(resp.model_dump())
            sse = _sse({
                "type": "done",
                "content": resp.deliverables[0],
                "sources": sources or [],
                "qualityScore": 85,
            })
            if resp.suggestions:
                labels = [s.label if hasattr(s, "label") else str(s) for s in resp.suggestions]
                sse += _sse({"type": "follow_up", "content": "\n".join(labels)})
            return sse
        return _sse({
            "type": "message_done",
            "content": inject_markers(resp.explanation, resp.model_dump()),
        })

    return event.to_sse()


def format_error_event(message: str) -> str:
    """Frame SSE pour signaler une erreur côté client (canal smart/swarm)."""
    return _sse({"type": "error", "message": message})


def format_swarm_event(event: ProgressEvent) -> str:
    """Variante /ai/actions/swarm : génération de document directe.

    Pas de phase conversationnelle : tous les 'done' sont des livrables.
    Une clarification éventuelle est renvoyée comme événement 'clarification'.
    """
    progress = _progress_payload(event, default_agent_id="document")
    if progress is not None:
        return _sse(progress)

    if event.type == EventType.done and event.agent_response:
        resp = event.agent_response
        if resp.clarifications:
            return _sse({"type": "clarification", "question": resp.clarifications[0]})

        content = resp.deliverables[0] if resp.deliverables else resp.explanation
        sources = format_sources(resp.model_dump())
        return _sse({
            "type": "done",
            "content": content,
            "sources": sources or [],
            "qualityScore": 85,
        })

    return event.to_sse()
