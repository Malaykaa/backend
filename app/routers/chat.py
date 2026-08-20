"""Couche d'adaptation frontend — expose les endpoints au format attendu par le React.

Ce router traduit les contrats entre le frontend existant et les services FastAPI.
Aucune logique métier ici (SRP) : on délègue tout aux services et on transforme les réponses.
Enregistré AVANT le chat router dans main.py pour prendre priorité sur les routes identiques.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from app.core.rate_limit import limiter, get_user_key
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload

from app.agents.events import EventType
from app.core.async_database import get_async_db
from app.core.database import get_db
from app.core.deps import extract_profile, get_async_current_user, get_current_user
from app.core.exceptions import NotFoundError
from app.core.preset_mapping import get_goal_type, get_preset_label, get_source_label
from app.models.chat import ChatMessage, ChatThread, MessageRole, ThreadStatus
from app.models.user import User
from app.schemas.chat import MessageEdit, ThreadUpdate
from app.services.chat_service import AsyncChatService, ChatService
from app.services.document_service import DocumentService
from app.services.goal_service import GoalService
from app.services.message_formatter import format_sources, inject_markers, strip_sources_block
from app.services.opportunity_service import OpportunityService
from app.services.plan_service import PlanService
from app.services.scraped_offer_service import ScrapedOfferService
from app.services.sse_formatter import SSE_HEADERS, format_chat_event
from app.repositories.intent_repo import IntentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ── Schémas de requête ──────────────────────────────────────


class FrontendThreadCreate(BaseModel):
    """Création de thread au format frontend (accepte presetKey)."""

    title: str | None = Field(None, max_length=300)
    instructions: str | None = None
    presetKey: str | None = None  # noqa: N815 — nom frontend
    orgId: str | None = None  # noqa: N815
    notifMode: str | None = None  # noqa: N815
    notifTime: str | None = None  # noqa: N815


class FrontendMessageCreate(BaseModel):
    """Envoi de message au format frontend.

    display_content : texte affiché dans la bulle utilisateur (optionnel).
        Si fourni, c'est lui qui est stocké en DB et montré à l'utilisateur.
        `content` reste le contexte complet envoyé au LLM (invisible).
        Utilisé par ex. pour les étapes du plan d'action : afficher "Étape : X"
        mais envoyer le contexte détaillé au modèle.
    """

    content: str = Field(..., min_length=1, max_length=8000)
    display_content: str | None = Field(default=None, max_length=500)
    useAi: bool = True  # noqa: N815
    history: list[dict] | None = None
    metadata: dict | None = None
    attachment_ids: list[str] | None = None  # UUIDs des pièces jointes uploadées
    # Référence d'une offre précise (carte affichée dans le chat) — ancre la
    # réponse sur cette seule offre. Cf. ScrapedOfferService.get_by_ref.
    offer_ref: str | None = Field(default=None, max_length=100)


# ── Schémas de réponse ──────────────────────────────────────


class FrontendMessage(BaseModel):
    id: str
    role: str
    content: str
    metadata: dict | None = None
    createdAt: str  # noqa: N815


class FrontendThread(BaseModel):
    id: str
    userId: str  # noqa: N815
    title: str
    source: str
    status: str
    instructions: str | None = None
    presetKey: str | None = None  # noqa: N815
    messages: list[FrontendMessage] = []
    unreadCount: int = 0  # noqa: N815 — messages assistant non lus depuis last_read_at
    createdAt: str  # noqa: N815
    updatedAt: str  # noqa: N815


class FrontendMessageResponse(BaseModel):
    content: str
    sources: list[dict] | None = None
    llmUsed: bool = True  # noqa: N815
    isDeliverable: bool | None = None  # noqa: N815
    explanation: str | None = None  # noqa: N815 — commentaire LLM quand isDeliverable


class FrontendPlanStep(BaseModel):
    id: str
    threadId: str  # noqa: N815
    step: int
    title: str
    description: str
    isMission: bool = False  # noqa: N815
    missionText: str | None = None  # noqa: N815
    completedAt: str | None = None  # noqa: N815
    createdAt: str  # noqa: N815


class FrontendPourMoiItem(BaseModel):
    id: str
    category: str
    title: str
    source: str | None = None
    url: str | None = None
    matchScore: int | None = None  # noqa: N815
    justification: str | None = None
    threadId: str | None = None  # noqa: N815
    status: str | None = None
    lastMessage: str | None = None  # noqa: N815
    createdAt: str  # noqa: N815
    offerRef: str | None = None  # noqa: N815 — pour le tracking feedback scraped offers


# ── Helpers de transformation ───────────────────────────────


def _iso(dt: datetime | None) -> str:
    """Convertit un datetime en ISO 8601 string."""
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    return dt.isoformat()


def _match_score_pct(offer: dict) -> int:
    """Pourcentage de correspondance affiché, sur l'échelle commune 0–100.

    Tolère les offres sérialisées avant l'introduction de `match_score` : elles
    proviennent du chemin sémantique, dont le score brut vivait sur 0–75.
    """
    value = offer.get("match_score")
    if value is None:
        value = float(offer.get("relevance_score") or 0.0) / 75.0 * 100.0
    return int(max(0.0, min(100.0, float(value))))


def _extract_preset_key(thread: ChatThread) -> str | None:
    """Extrait le presetKey depuis le goal.context_data du thread."""
    if thread.goal and thread.goal.context_data:
        return thread.goal.context_data.get("preset_key")
    return None


def _get_source(thread: ChatThread) -> str:
    """Détermine le champ 'source' pour le frontend."""
    if thread.goal and thread.goal.type:
        return get_source_label(thread.goal.type)
    return "99eAnge"


def _get_instructions(thread: ChatThread) -> str | None:
    """Extrait les instructions depuis le goal.context_data."""
    if thread.goal and thread.goal.context_data:
        return thread.goal.context_data.get("instructions")
    return None


def _format_message(msg, inject: bool = True) -> FrontendMessage:
    """Transforme un ChatMessage ORM en FrontendMessage."""
    # Toujours nettoyer les blocs Sources — qu'il y ait un payload ou pas,
    # et quelle que soit la version du code qui a sauvegardé ce message.
    content = strip_sources_block(msg.content)
    metadata: dict | None = None

    if inject and msg.payload:
        raw_deliverables = msg.payload.get("deliverables") or []
        # Real deliverables are actual document text (long), not preset keys like "cover_letter"
        real_deliverables = [d for d in raw_deliverables if isinstance(d, str) and len(d) > 100]

        sources = format_sources(msg.payload)
        meta_parts: dict = {}
        if sources:
            meta_parts["sources"] = sources

        if real_deliverables:
            # Document message: serve the actual document content — no markers needed
            content = "\n\n".join(real_deliverables)
            meta_parts["isDeliverable"] = True
        else:
            # Conversational message: inject interactive step/suggestion markers
            content = inject_markers(content, msg.payload)

        # Étape de plan d'action complétée — clé composite persistée côté serveur
        # pour permettre la synchronisation multi-appareils sans localStorage.
        step_key = msg.payload.get("completed_step_key")
        if step_key:
            meta_parts["completedStepKey"] = step_key

        if meta_parts:
            metadata = meta_parts

    return FrontendMessage(
        id=str(msg.id),
        role=msg.role.value,
        content=content,
        metadata=metadata,
        createdAt=_iso(msg.created_at),
    )


def _is_internal_message(msg) -> bool:
    """Détecte les messages internes (contexte auto-généré) à ne pas afficher."""
    # Nouveaux messages : flag explicite dans le payload
    if msg.payload and msg.payload.get("is_internal"):
        return True
    # Legacy : messages de contexte générés lors de la création d'un thread preset
    if (
        msg.role == MessageRole.user
        and msg.content
        and msg.content.startswith("Nouvel objectif : ")
        and "Pose les questions diagnostiques" in msg.content
    ):
        return True
    return False


def _format_thread(thread: ChatThread, include_messages: bool = False) -> FrontendThread:
    """Transforme un ChatThread ORM en FrontendThread (format frontend)."""
    visible_msgs = [
        m for m in (thread.messages or [])
        if m.is_active and not _is_internal_message(m)
    ]

    messages: list[FrontendMessage] = []
    if include_messages:
        messages = [_format_message(m) for m in visible_msgs]

    # Calculer updatedAt : dernier message actif ou created_at
    updated_at = thread.created_at
    active_all = [m for m in (thread.messages or []) if m.is_active]
    if active_all:
        updated_at = active_all[-1].created_at

    # Compter les messages assistant non lus depuis last_read_at
    last_read = thread.last_read_at
    unread_count = sum(
        1 for m in visible_msgs
        if m.role == MessageRole.assistant
        and (last_read is None or m.created_at > last_read)
    )

    return FrontendThread(
        id=str(thread.id),
        userId=str(thread.user_id),
        title=thread.title or "",
        source=_get_source(thread),
        status=thread.status.value,
        instructions=_get_instructions(thread),
        presetKey=_extract_preset_key(thread),
        messages=messages,
        unreadCount=unread_count,
        createdAt=_iso(thread.created_at),
        updatedAt=_iso(updated_at),
    )


# ── Endpoints ───────────────────────────────────────────────


@router.post("/threads", response_model=FrontendThread, status_code=201)
async def create_thread(
    body: FrontendThreadCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Crée un thread. Si presetKey fourni, crée aussi un Goal et déclenche l'IA."""
    chat_svc = ChatService(db)
    profile = extract_profile(current_user)

    if body.presetKey:
        # 1. Mapper presetKey → GoalType et créer le Goal
        goal_type = get_goal_type(body.presetKey)
        preset_label = get_preset_label(body.presetKey)

        goal_svc = GoalService(db)
        # Titre du thread = ce que l'utilisateur a tapé (title ou instructions), sinon le label du preset
        thread_title = (body.title or body.instructions or "").strip() or preset_label
        goal = goal_svc.create_goal(
            user_id=current_user.id,
            goal_type=goal_type,
            context_data={
                "preset_key": body.presetKey,
                "preset_label": preset_label,
                "instructions": body.instructions,
                "notif_mode": body.notifMode,
                "notif_time": body.notifTime,
            },
            title=thread_title,
        )

        # GoalService.create_goal crée déjà un thread lié → le récupérer via requête
        # (la relation lazy goal.threads n'est pas fiable après un flush cross-repo)
        thread = chat_svc.repo.get_thread_for_goal(goal.id, current_user.id)
        if not thread:
            thread = chat_svc.create_thread(
                user_id=current_user.id,
                title=thread_title,
                goal_id=goal.id,
            )

        # Commit thread + goal AVANT l'appel IA pour que le thread survive
        # même si l'orchestrateur échoue (ModuleNotFoundError, timeout, etc.)
        db.commit()
        thread_id = thread.id

        # 2. Envoyer un message contextuel à l'orchestrateur pour déclencher la réponse IA
        first_name = profile.get("first_name", "")
        context_msg = f"Nouvel objectif : {preset_label}."
        if body.instructions:
            context_msg += f" Contexte : {body.instructions}"
        if first_name:
            context_msg += f" (utilisateur : {first_name})"
        context_msg += " Pose les questions diagnostiques adaptées à ce profil."

        try:
            _assistant_msg, _agent_resp = await chat_svc.handle_message(
                thread_id=thread_id,
                user_id=current_user.id,
                content=context_msg,
                profile=profile,
                user_payload={"is_internal": True},
            )
            db.commit()
        except Exception:
            logger.exception("IA welcome failed for thread %s", thread_id)
            db.rollback()
            # Fallback : message d'accueil minimal pour ne pas laisser le thread vide
            # (sinon le frontend reste bloqué sur "⏳ Préparation…" indéfiniment)
            greeting = f"Bonjour {first_name} !" if first_name else "Bonjour !"
            fallback_content = (
                f"{greeting}\n\n"
                f"Ton objectif **{thread_title}** a bien été créé. "
                "Comment souhaites-tu commencer ?"
            )
            db.add(ChatMessage(
                thread_id=thread_id,
                role=MessageRole.assistant,
                content=fallback_content,
            ))
            db.commit()

        # Recharger le thread avec ses messages (joinedload)
        thread = (
            db.execute(
                sa_select(ChatThread)
                .options(joinedload(ChatThread.goal), joinedload(ChatThread.messages))
                .where(ChatThread.id == thread_id)
            )
            .unique()
            .scalar_one()
        )
        return _format_thread(thread, include_messages=True)

    # Pas de presetKey → thread simple
    thread = chat_svc.create_thread(
        user_id=current_user.id,
        title=body.title,
    )
    db.commit()
    return _format_thread(thread, include_messages=True)


@router.get("/threads", response_model=list[FrontendThread])
def list_threads(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Liste les threads enrichis avec presetKey, source, dernier message."""
    # Charger les threads avec le goal joinedload pour éviter N+1
    stmt = (
        sa_select(ChatThread)
        .options(joinedload(ChatThread.goal), joinedload(ChatThread.messages))
        .where(ChatThread.user_id == current_user.id)
        .order_by(ChatThread.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    threads = db.execute(stmt).unique().scalars().all()
    return [_format_thread(t, include_messages=True) for t in threads]


@router.get("/threads/{thread_id}", response_model=FrontendThread)
def get_thread(
    thread_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Récupère un thread avec messages enrichis (marqueurs injectés).

    Met à jour last_read_at pour réinitialiser le compteur de non-lus.
    """
    stmt = (
        sa_select(ChatThread)
        .options(joinedload(ChatThread.goal), joinedload(ChatThread.messages))
        .where(ChatThread.id == thread_id)
    )
    thread = db.execute(stmt).unique().scalar_one_or_none()
    if not thread or thread.user_id != current_user.id:
        raise NotFoundError("Thread")
    thread.last_read_at = datetime.now(timezone.utc)
    db.commit()
    return _format_thread(thread, include_messages=True)


@router.post("/threads/{thread_id}/messages", response_model=FrontendMessageResponse)
async def send_message(
    thread_id: uuid.UUID,
    body: FrontendMessageCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Envoie un message et retourne la réponse au format frontend."""
    chat_svc = ChatService(db)
    profile = extract_profile(current_user)

    assistant_msg, agent_response = await chat_svc.handle_message(
        thread_id=thread_id,
        user_id=current_user.id,
        content=body.content,
        profile=profile,
        attachment_ids=body.attachment_ids,
        offer_ref=body.offer_ref,
    )

    # Auto-persist plan si l'agent retourne des étapes et que le goal n'a pas encore de plan
    PlanService(db).maybe_persist_from_response(thread_id, current_user.id, agent_response)

    db.commit()

    # Transformer la réponse au format frontend.
    # Quand un livrable est généré, le frontend l'affiche dans une card séparée
    # → renvoyer UNIQUEMENT le contenu du livrable (pas l'explanation mélangée).
    sources = format_sources(agent_response.model_dump())
    is_deliverable = bool(agent_response.deliverables) or None
    explanation_text: str | None = None

    if agent_response.deliverables:
        # Livrable → contenu brut du document (le frontend gère le rendu card/modal)
        display_content = "\n\n".join(agent_response.deliverables)
        # Explication du LLM → affichée au-dessus de la card côté frontend
        explanation_text = inject_markers(
            agent_response.explanation, agent_response.model_dump()
        )
    else:
        # Conversationnel → explanation enrichie de markers (steps, propositions…)
        display_content = inject_markers(
            agent_response.explanation, agent_response.model_dump()
        )

    return FrontendMessageResponse(
        content=display_content,
        sources=sources,
        llmUsed=True,
        isDeliverable=is_deliverable,
        explanation=explanation_text,
    )


@router.patch("/threads/{thread_id}", response_model=FrontendThread)
def rename_thread(
    thread_id: uuid.UUID,
    body: ThreadUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Renomme un thread."""
    chat_svc = ChatService(db)
    thread = chat_svc.rename_thread(thread_id, current_user.id, body.title)
    db.commit()
    return _format_thread(thread, include_messages=False)


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread(
    thread_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Supprime un thread."""
    chat_svc = ChatService(db)
    chat_svc.delete_thread(thread_id, current_user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/threads/{thread_id}/messages/{message_id}",
    response_model=FrontendMessage,
)
def edit_message(
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageEdit,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Édite un message utilisateur (cascade les suivants en soft-delete)."""
    chat_svc = ChatService(db)
    msg = chat_svc.edit_message(
        message_id=message_id,
        thread_id=thread_id,
        user_id=current_user.id,
        new_content=body.content,
    )
    db.commit()
    return _format_message(msg, inject=False)


@router.delete(
    "/threads/{thread_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_message(
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Supprime (soft) un message user + la réponse assistant qui suit."""
    chat_svc = ChatService(db)
    chat_svc.delete_message(
        message_id=message_id,
        thread_id=thread_id,
        user_id=current_user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/threads/{thread_id}/stream")
@limiter.limit("20/minute", key_func=get_user_key)
async def stream_message(
    request: Request,
    thread_id: uuid.UUID,
    body: FrontendMessageCreate,
    current_user: Annotated[User, Depends(get_async_current_user)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
):
    """Streaming SSE — async DB (asyncpg) pour ne jamais bloquer la boucle asyncio.

    Mapping des événements :
        planning     → generating
        step_start   → agent_start + {agentId, label}
        step_complete → agent_done + {agentId}
        chunk        → token + {token}
        done         → done + {content, sources, qualityScore}

    PlanService (sync) est exécuté dans un thread propre après le stream,
    en attendant sa migration async.
    """
    import asyncio as _asyncio

    chat_svc = AsyncChatService(db)
    profile = extract_profile(current_user)
    _user_id = current_user.id

    async def transform_events():
        async for event in chat_svc.stream_message(
            thread_id=thread_id,
            user_id=_user_id,
            content=body.content,
            profile=profile,
            attachment_ids=body.attachment_ids,
            display_content=body.display_content,
            user_payload=body.metadata,
            offer_ref=body.offer_ref,
        ):
            if event.type == EventType.done and event.agent_response:
                _resp_snapshot = event.agent_response
                await _asyncio.to_thread(
                    _sync_persist_plan, thread_id, _user_id, _resp_snapshot,
                )
                # Commit ici : user_msg + assistant_msg sont déjà flushés par _save_assistant.
                # Committer avant le yield garantit que les messages sont en DB même si le
                # client se déconnecte après réception du done (navigation, fermeture onglet).
                await db.commit()
            yield format_chat_event(event)

    return StreamingResponse(
        transform_events(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# ── Plan (lié au thread via le goal) ────────────────────────


@router.get("/threads/{thread_id}/plan", response_model=list[FrontendPlanStep])
def get_thread_plan(
    thread_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Retourne les étapes du plan associé au thread (via le goal)."""
    stmt = (
        sa_select(ChatThread)
        .options(joinedload(ChatThread.goal))
        .where(ChatThread.id == thread_id)
    )
    thread = db.execute(stmt).unique().scalar_one_or_none()
    if not thread or thread.user_id != current_user.id:
        raise NotFoundError("Thread")

    # Pas de goal → pas de plan → liste vide
    if not thread.goal:
        return []

    plan_svc = PlanService(db)
    try:
        plan = plan_svc.get_plan(thread.goal_id, current_user.id)
    except NotFoundError:
        return []

    return [
        FrontendPlanStep(
            id=str(step.id),
            threadId=str(thread_id),
            step=step.order,
            title=step.label,
            description=step.description,
            isMission=False,
            missionText=None,
            completedAt=_iso(datetime.now(timezone.utc)) if step.status.value == "done" else None,
            createdAt=_iso(plan.created_at),
        )
        for step in plan.steps
    ]


@router.patch("/threads/{thread_id}/plan/{step_id}/complete", response_model=FrontendPlanStep)
def complete_plan_step(
    thread_id: uuid.UUID,
    step_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Marque une étape du plan comme terminée."""
    stmt = (
        sa_select(ChatThread)
        .options(joinedload(ChatThread.goal))
        .where(ChatThread.id == thread_id)
    )
    thread = db.execute(stmt).unique().scalar_one_or_none()
    if not thread or thread.user_id != current_user.id:
        raise NotFoundError("Thread")
    if not thread.goal:
        raise NotFoundError("Objectif associé")

    plan_svc = PlanService(db)
    step = plan_svc.complete_step(thread.goal_id, step_id, current_user.id)
    db.commit()

    return FrontendPlanStep(
        id=str(step.id),
        threadId=str(thread_id),
        step=step.order,
        title=step.label,
        description=step.description,
        isMission=False,
        missionText=None,
        completedAt=_iso(datetime.now(timezone.utc)),
        createdAt=_iso(datetime.now(timezone.utc)),
    )


# ── Pour Moi feed ──────────────────────────────────────────


@router.get("/pour-moi", response_model=list[FrontendPourMoiItem])
async def get_pour_moi(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Feed Pour Moi unifié : objectifs + recommandations + documents."""
    items: list[FrontendPourMoiItem] = []
    profile = extract_profile(current_user)

    # 1. Objectifs (threads avec goal)
    goal_svc = GoalService(db)
    goals = goal_svc.get_goals(current_user.id)

    chat_svc = ChatService(db)
    for goal in goals:
        # Trouver le thread lié
        thread = chat_svc.repo.get_thread_for_goal(goal.id, current_user.id)
        preset_key = (goal.context_data or {}).get("preset_key")
        label = (goal.context_data or {}).get("preset_label") or get_preset_label(preset_key or "")

        # Dernier message pour l'aperçu
        last_msg = None
        if thread and thread.messages:
            active = [m for m in thread.messages if m.is_active]
            if active:
                last_msg = active[-1].content[:100]

        items.append(FrontendPourMoiItem(
            id=str(goal.id),
            category="Objectif",
            title=label or goal.type.value,
            source=get_source_label(goal.type),
            threadId=str(thread.id) if thread else None,
            status=goal.status.value,
            lastMessage=last_msg,
            createdAt=_iso(goal.created_at),
        ))

    # 2. Recommandations (opportunités matchées)
    opp_svc = OpportunityService(db)
    matches = opp_svc.match_for_user(current_user.id, current_user.profile)

    for match in matches[:20]:  # Limiter pour la perf
        opp = match["opportunity"]
        items.append(FrontendPourMoiItem(
            id=str(opp.id),
            category="Recommandation",
            title=opp.title,
            source=opp.domain or "Opportunité",
            url=opp.source_url,
            matchScore=int(match["score"]),
            status=match["status"].value if hasattr(match["status"], "value") else str(match["status"]),
            createdAt=_iso(opp.created_at),
        ))

    # 3. Propositions — offres scrapées matchant les intentions extraites
    intent_repo = IntentRepository(db)
    intents = intent_repo.get_latest_for_user(current_user.id, limit=3)
    if intents:
        offer_svc = ScrapedOfferService(db)
        scraped = await offer_svc.search_for_matching(intents[0], limit=8)
        for offer in scraped:
            offer_ref = offer.get("offer_ref", "")
            company = offer.get("company") or ""
            location = offer.get("location") or ""
            justification = f"{company} · {location}".strip(" ·") or None
            items.append(FrontendPourMoiItem(
                id=offer_ref or f"scraped-{offer.get('id','')}",
                category="Proposition",
                title=offer.get("title", ""),
                source=offer.get("type"),
                url=offer.get("url"),
                # `match_score` est normalisé 0–100 par ScrapedOfferService et
                # comparable entre modes. L'ancien `relevance_score * 2.5`
                # supposait une échelle 0–40, qui ne correspondait à aucune des
                # deux branches de recherche (0–75 sémantique, 0–25 lexicale).
                matchScore=_match_score_pct(offer),
                justification=justification or None,
                offerRef=offer_ref or None,
                createdAt=_iso(offer.get("scraped_at")),
            ))

    # 4. Documents générés
    doc_svc = DocumentService(db)
    documents = doc_svc.list_documents(current_user.id, limit=10, offset=0)

    for doc in documents:
        items.append(FrontendPourMoiItem(
            id=str(doc.id),
            category="Daily Infos",
            title=f"Document : {doc.type.value}",
            status="ready",
            createdAt=_iso(doc.created_at),
        ))

    # Trier par date décroissante
    items.sort(key=lambda x: x.createdAt, reverse=True)
    return items


# ── Close thread ────────────────────────────────────────────


@router.post("/threads/{thread_id}/close")
def close_thread(
    thread_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Ferme un thread (status → closed)."""
    chat_svc = ChatService(db)
    thread = chat_svc.get_thread(thread_id, current_user.id)
    thread.status = ThreadStatus.closed
    db.commit()
    return {"ok": True}


# ── Helper sync pour PlanService (exécuté via asyncio.to_thread) ─────────────


def _sync_persist_plan(
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_response,
) -> None:
    """Persiste le plan depuis la réponse agent (PlanService est sync).

    Exécuté dans un thread isolé avec une session sync propre,
    en attendant la migration async de PlanService.
    """
    from app.core.database import SessionLocal

    with SessionLocal() as sync_db:
        try:
            PlanService(sync_db).maybe_persist_from_response(thread_id, user_id, agent_response)
            sync_db.commit()
        except Exception as exc:
            logger.warning(
                "[stream_message] Échec persistance plan thread %s : %s", thread_id, exc,
            )
            sync_db.rollback()
