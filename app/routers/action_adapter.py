"""Adapter actions/livrables — expose les endpoints SSE au format attendu par le frontend.

Deux endpoints :
- POST /ai/actions/{preset}/smart/stream  — conversationnel ou livrable (le backend décide)
- POST /ai/actions/swarm/{action_type}/stream — génération de document directe

Réutilise le pipeline Orchestrator → ExecutionEngine existant.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.base import AgentContext
from app.agents.deliverable_configs import DELIVERABLE_CONFIGS, detect_doc_type
from app.agents.events import EventType
from app.agents.orchestrator import Orchestrator
from app.core.database import get_db
from app.core.deps import extract_profile, get_current_user
from app.llm import get_llm_provider
from app.models.user import User
from app.services.scraped_offer_service import ScrapedOfferService
from app.services.sse_formatter import (
    SSE_HEADERS,
    format_error_event,
    format_smart_event,
    format_swarm_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["action-adapter"])


# ── Schémas de requête ──────────────────────────────────────


class SmartStreamRequest(BaseModel):
    """Corps de POST /ai/actions/{preset}/smart/stream."""

    message: str = Field(..., min_length=1, max_length=5000)
    history: list[dict] | None = None
    objectiveContext: str | None = None  # noqa: N815
    docCount: int | None = None  # noqa: N815
    imageCount: int | None = None  # noqa: N815


class SwarmStreamRequest(BaseModel):
    """Corps de POST /ai/actions/swarm/{action_type}/stream."""

    customInstructions: str | None = None  # noqa: N815
    objectiveContext: str | None = None  # noqa: N815
    threadId: str | None = None  # noqa: N815 — optionnel : thread où persister le document généré


# ── Mapping action_type → doc_type connu ────────────────────

# Le frontend normalise les presets en action_types via PRESET_TO_ACTION_TYPE.
# Côté backend, on mappe vers les clés de DELIVERABLE_CONFIGS.
_ACTION_TO_DOC_TYPE: dict[str, str] = {
    "business_plan": "business_plan",
    "cv_lettre": "cv",  # défaut CV ; cover_letter détecté via message
    "memoire_rapport": "report",
    "document_general": "report",
    "proposition_commerciale": "proposition_commerciale",
    "contrat": "contract",
    "plan_marketing": "plan_marketing",
    "etude_marche": "etude_marche",
    # Identités directes (le frontend peut envoyer le doc_type tel quel)
    "cv": "cv",
    "cover_letter": "cover_letter",
    "contract": "contract",
    "report": "report",
    "pitch_deck": "pitch_deck",
    "email_pro": "email_pro",
    "study_sheet": "study_sheet",
    "politique_rh": "politique_rh",
    # Presets bruts envoyés par le frontend (non normalisés)
    "business_plan_swarm": "business_plan",
    "montage_projet": "report",
    "mini_memoire": "report",
    "dissertation": "report",
    "expose": "report",
    "rapports": "report",
    "analyse_cv": "cv",
    "simulation_entretien": "cv",
    "recherche_partenaire": "report",
    "prospection_commerciale": "proposition_commerciale",
    "proposition_contrat": "contract",
    # ── Presets ActionsTab frontend (clés exactes envoyées par le frontend) ──
    "marketing_plan":      "plan_marketing",
    "market_study":        "etude_marche",
    "project_setup":       "report",
    "thesis":              "report",
    "cv_analysis":         "cv",
    "interview_sim":       "cv",
    "commercial_proposal": "proposition_commerciale",
    "contract_proposal":   "contract",
}

# Presets conversationnels → goal_type fallback quand pas de doc_type.
# Permet à l'orchestrateur de router vers le bon agent spécialisé
# SANS passer par le triage (qui peut rejeter les messages courts).
_PRESET_TO_GOAL_TYPE: dict[str, str] = {
    "montage_projet": "funding",
    "business_plan_swarm": "funding",
    "plan_marketing": "funding",
    "etude_marche": "funding",
    "rapports": "exam",
    "mini_memoire": "exam",
    "dissertation": "exam",
    "expose": "exam",
    "analyse_cv": "career",
    "simulation_entretien": "career",
    "recherche_partenaire": "tender",
    "prospection_commerciale": "tender",
    "proposition_commerciale": "tender",
    "proposition_contrat": "tender",
    # Presets ActionsTab frontend
    "marketing_plan":      "funding",
    "market_study":        "funding",
    "project_setup":       "funding",
    "thesis":              "exam",
    "cv_analysis":         "career",
    "interview_sim":       "career",
    "commercial_proposal": "tender",
    "contract_proposal":   "tender",
}


def _normalize_preset(preset: str) -> str:
    """Normalise le preset brut envoyé par le frontend.

    Supprime le suffixe _swarm et mappe les alias courants.
    """
    cleaned = preset.removesuffix("_swarm")
    return cleaned


def _resolve_doc_type(action_type: str, message: str) -> str | None:
    """Résout le type de document depuis l'action_type et le message.

    1. Cherche dans le mapping explicite (brut puis normalisé)
    2. Tente la détection heuristique depuis le message
    3. Retourne None si rien ne matche (le triage décidera)
    """
    # Si c'est cv_lettre, affiner via les mots-clés du message
    if action_type == "cv_lettre":
        detected = detect_doc_type(message)
        if detected in ("cv", "cover_letter"):
            return detected
        return "cv"  # défaut

    # Essayer le mapping direct
    if action_type in _ACTION_TO_DOC_TYPE:
        return _ACTION_TO_DOC_TYPE[action_type]

    # Essayer avec le preset normalisé (ex: business_plan_swarm → business_plan)
    normalized = _normalize_preset(action_type)
    if normalized != action_type and normalized in _ACTION_TO_DOC_TYPE:
        return _ACTION_TO_DOC_TYPE[normalized]

    # Clé directe dans DELIVERABLE_CONFIGS ?
    if action_type in DELIVERABLE_CONFIGS:
        return action_type

    # Détection heuristique depuis le message
    return detect_doc_type(message)


# ── Endpoints ───────────────────────────────────────────────


@router.post("/actions/{preset}/smart/stream")
async def smart_stream(
    preset: str,
    body: SmartStreamRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Smart endpoint : laisse l'orchestrateur décider entre conversationnel et livrable.

    Le frontend envoie le preset brut (ex: 'business_plan_swarm', 'topic5', 'cv').
    Le backend résout le type de document si applicable, puis route via l'Orchestrator.
    """
    profile = extract_profile(current_user)
    doc_type = _resolve_doc_type(preset, body.message)

    # Construire le contexte agent
    goal_context: dict = {}
    if doc_type:
        goal_context["document_type"] = doc_type
    if body.objectiveContext:
        goal_context["objective_context"] = body.objectiveContext

    history = body.history or []

    # ── Intent-aware goal_type resolution ──
    # Si l'historique contient déjà des messages assistant (conversation en cours),
    # ne pas forcer goal_type="document" : laisser le Triage LLM décider si le
    # message est une demande de document ou une question conversationnelle.
    # Cela évite que "où postuler ?" après un CV génère un nouveau document.
    has_prior_conversation = any(
        h.get("role") in ("assistant", "bot") for h in history
    )

    goal_type: str | None = None
    if doc_type and not has_prior_conversation:
        # Premier message → l'utilisateur veut clairement un document
        goal_type = "document"
    elif not doc_type and preset in _PRESET_TO_GOAL_TYPE:
        goal_type = _PRESET_TO_GOAL_TYPE[preset]
    # Sinon goal_type = None → le Triage LLM décidera

    # Enrichir avec des offres scrapées pertinentes (si disponibles)
    if goal_type and goal_type not in ("document", "free"):
        try:
            offers = ScrapedOfferService(db).search_for_agent(
                intent=goal_type, profile=profile, message=body.message,
            )
            if offers:
                goal_context["relevant_offers"] = offers
        except Exception:
            logger.debug("Offer enrichment skipped in smart_stream", exc_info=True)

    ctx = AgentContext(
        user_id=current_user.id,
        message=body.message,
        history=history,
        profile=profile,
        goal_type=goal_type,
        goal_context=goal_context,
    )

    async def stream():
        llm = get_llm_provider()
        orchestrator = Orchestrator(llm)
        try:
            async for event in orchestrator.stream_route(ctx):
                yield format_smart_event(event)
        except Exception as exc:
            logger.error("Smart stream failed for preset=%s: %s", preset, exc)
            yield format_error_event("Une erreur est survenue. Réessaie dans quelques instants.")

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/actions/swarm/{action_type}/stream")
async def swarm_stream(
    action_type: str,
    body: SwarmStreamRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Legacy swarm endpoint : génération de document directe (pas de phase conversationnelle).

    Le frontend envoie l'action_type normalisé (ex: 'business_plan', 'memoire_rapport').
    """
    profile = extract_profile(current_user)
    message = body.customInstructions or f"Génère un document de type {action_type}."
    doc_type = _resolve_doc_type(action_type, message)

    goal_context: dict = {"document_type": doc_type or action_type}
    if body.objectiveContext:
        goal_context["objective_context"] = body.objectiveContext

    ctx = AgentContext(
        user_id=current_user.id,
        message=message,
        history=[],
        profile=profile,
        goal_type="document",
        goal_context=goal_context,
    )

    # Parsing du thread_id pour la persistance post-génération
    thread_uuid: _uuid.UUID | None = None
    if body.threadId:
        try:
            thread_uuid = _uuid.UUID(body.threadId)
        except ValueError:
            logger.warning("[swarm_stream] threadId invalide ignoré : %s", body.threadId)

    async def stream():
        llm = get_llm_provider()
        orchestrator = Orchestrator(llm)
        final_content: str = ""
        try:
            async for event in orchestrator.stream_route(ctx):
                yield format_swarm_event(event)
                # Capturer le contenu final pour persistance après streaming
                if event.type == EventType.done and event.agent_response:
                    resp = event.agent_response
                    if resp.deliverables:
                        final_content = resp.deliverables[0]
        except Exception as exc:
            logger.error("Swarm stream failed for action_type=%s: %s", action_type, exc)
            yield format_error_event("La génération a échoué. Réessaie dans quelques instants.")

        # Persister le document dans le thread après la fin du streaming.
        # Utilise le même format que _save_assistant() (payload.deliverables)
        # pour que _format_message() en chat.py le rende comme livrable.
        if thread_uuid and final_content:
            try:
                from app.repositories.chat_repo import ChatRepository
                from app.models.chat import MessageRole
                repo = ChatRepository(db)
                repo.add_message(
                    thread_id=thread_uuid,
                    role=MessageRole.assistant,
                    content=final_content[:200],  # résumé court dans content
                    payload={
                        "deliverables": [final_content],
                        "agent_id": "document",
                    },
                )
                db.commit()
                logger.info(
                    "[swarm_stream] Document persisté dans thread %s (%d chars)",
                    thread_uuid, len(final_content),
                )
            except Exception as save_exc:
                logger.warning(
                    "[swarm_stream] Échec persistance document thread %s : %s",
                    thread_uuid, save_exc,
                )
                db.rollback()

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
