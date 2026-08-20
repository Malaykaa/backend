"""Service chat — logique métier : save message, orchestrate, save response.

Deux variantes :
- ChatService      : sync DB (APScheduler, `def` routers, `create_thread`)
- AsyncChatService : async DB asyncpg — routers SSE critiques (`stream_message`)

Deux modes (chaque variante) :
- handle_message()  : retourne (ChatMessage, AgentResponse) — mode classique
- stream_message()  : yield des ProgressEvent en SSE — mode streaming
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

import structlog
from app.core.logging_config import bind_chat_context

logger = structlog.get_logger(__name__)

from app.agents.base import AgentContext, AgentResponse
from app.agents.events import EventType, ProgressEvent
from app.agents.orchestrator import Orchestrator
from app.agents.types import GoalType
from app.core.exceptions import NotFoundError
from app.llm import get_llm_provider
from app.models.chat import ChatMessage, ChatThread, MessageRole
from app.repositories.chat_repo import AsyncChatRepository, ChatRepository
from app.services.intent_extractor import IntentExtractorService
from app.services.memory_service import AsyncMemoryService, MemoryService
from app.services.career_reference_service import CareerReferenceService
from app.services.scraped_offer_service import ScrapedOfferService


class ChatService:
    def __init__(self, db: Session) -> None:
        self.repo = ChatRepository(db)
        self.memory = MemoryService(db)
        self.intent_extractor = IntentExtractorService(db)

    def create_thread(
        self,
        user_id: uuid.UUID,
        title: str | None = None,
        goal_id: uuid.UUID | None = None,
    ) -> ChatThread:
        return self.repo.create_thread(user_id=user_id, title=title, goal_id=goal_id)

    def get_thread(self, thread_id: uuid.UUID, user_id: uuid.UUID) -> ChatThread:
        thread = self.repo.get_thread_with_messages(thread_id)
        if not thread or thread.user_id != user_id:
            raise NotFoundError("Thread")
        return thread

    def list_threads(self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[ChatThread]:
        return self.repo.get_user_threads(user_id, limit=limit, offset=offset)

    def rename_thread(self, thread_id: uuid.UUID, user_id: uuid.UUID, title: str) -> ChatThread:
        thread = self.repo.get_by_id(thread_id)
        if not thread or thread.user_id != user_id:
            raise NotFoundError("Thread")
        thread.title = title
        self.repo.save(thread)
        return thread

    def delete_thread(self, thread_id: uuid.UUID, user_id: uuid.UUID) -> None:
        thread = self.repo.get_by_id(thread_id)
        if not thread or thread.user_id != user_id:
            raise NotFoundError("Thread")
        self.repo.delete(thread)

    def _prepare_context(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        profile: dict | None,
        user_payload: dict | None = None,
        attachment_ids: list[str] | None = None,
        display_content: str | None = None,
        offer_ref: str | None = None,
    ) -> tuple[ChatThread, AgentContext]:
        """Étapes communes à handle_message/stream_message :
        vérif ownership, mémoire, save user msg, construction de l'AgentContext.

        display_content : si fourni, c'est ce texte court qui est stocké en DB
            (bulle utilisateur visible). `content` reste le contexte complet
            envoyé au LLM. Utilisé par les étapes du plan d'action.
        offer_ref : quand le client a cliqué une action liée à UNE offre
            précise (carte affichée dans le chat) — ancre le tour sur cette
            seule offre plutôt que la recherche générique par intention/pays.
        """
        thread = self.repo.get_thread_with_messages(thread_id)
        if not thread or thread.user_id != user_id:
            raise NotFoundError("Thread")

        # Mémoire AVANT d'ajouter le message user (le courant ira dans ctx.message)
        memory_ctx = self.memory.get_context(thread_id)
        history = memory_ctx["recent_messages"]
        summary = memory_ctx["summary"]

        # En DB : on stocke display_content (si fourni) pour la bulle utilisateur
        stored_content = display_content if display_content else content
        user_msg = self.repo.add_message(
            thread_id=thread_id, role=MessageRole.user,
            content=stored_content, payload=user_payload,
        )

        # Lier les pièces jointes pending et enrichir le contenu avec le texte extrait / images
        enriched_content = content
        image_data: list[dict] = []
        if attachment_ids:
            from app.services.document_service import DocumentService, ATTACHMENTS_DIR
            import base64
            doc_svc = DocumentService(self.repo.db)
            parsed_ids: list[uuid.UUID] = []
            for raw_id in attachment_ids:
                try:
                    parsed_ids.append(uuid.UUID(raw_id))
                except ValueError:
                    pass
            attachments = doc_svc.link_attachments_to_message(parsed_ids, user_msg.id, user_id)
            texts: list[str] = []
            for att in attachments:
                if att.extracted_text:
                    texts.append(
                        f"[Contenu du fichier '{att.filename}' :\n{att.extracted_text}]"
                    )
                elif att.content_type.startswith("image/"):
                    try:
                        file_path = ATTACHMENTS_DIR / att.storage_key
                        raw_bytes = file_path.read_bytes()
                        image_data.append({
                            "media_type": att.content_type,
                            "data": base64.standard_b64encode(raw_bytes).decode(),
                        })
                    except Exception as exc:
                        logger.warning("Failed to load image attachment %s: %s", att.id, exc)
            if texts:
                enriched_content = "\n\n".join(texts) + "\n\n" + content

        # goal_type : priorité au goal lié, sinon dernier agent_id de l'historique
        goal_type = None
        goal_type_source = None
        if thread.goal and thread.goal.type:
            goal_type = thread.goal.type.value
            goal_type_source = "goal"
        elif history:
            goal_type = self._extract_last_agent_id(thread_id)
            if goal_type:
                goal_type_source = "inferred"

        # Résumé compressé injecté en system message
        effective_history = history
        if summary:
            effective_history = [
                {"role": "system", "content": f"Résumé de la conversation précédente : {summary}"},
                *history,
            ]

        goal_context = {}
        if thread.goal and thread.goal.context_data:
            goal_context = thread.goal.context_data
        goal_context = self._enrich_with_offers(
            goal_context, goal_type, profile or {}, content, self.repo.db,
            offer_ref=offer_ref,
        )
        goal_context = self._enrich_with_careers(
            goal_context, goal_type, profile or {}, content, self.repo.db,
        )

        ctx = AgentContext(
            user_id=user_id,
            message=enriched_content,  # contient le texte des PDFs si présents
            history=effective_history,
            profile=profile or {},
            goal_type=goal_type,
            goal_type_source=goal_type_source,
            goal_context=goal_context,
            image_data=image_data,
        )
        return thread, ctx

    def _save_assistant(
        self,
        thread: ChatThread,
        agent_response: AgentResponse,
        original_content: str,
    ) -> ChatMessage:
        """Persiste la réponse assistant et met à jour le titre du thread si vide."""
        msg = self.repo.add_message(
            thread_id=thread.id,
            role=MessageRole.assistant,
            content=agent_response.explanation,
            payload=agent_response.model_dump(),
        )
        if not thread.title and original_content:
            thread.title = original_content[:100]
            self.repo.save(thread)
        return msg

    async def _post_process(self, thread_id: uuid.UUID, user_id: uuid.UUID, llm) -> None:
        """Compression mémoire + extraction d'intention (fire-and-forget)."""
        try:
            await self.memory.maybe_compress(thread_id, llm)
        except Exception as exc:
            logger.warning("Memory compression failed for thread %s: %s", thread_id, exc)
        try:
            await self.intent_extractor.maybe_extract(thread_id, user_id, llm)
        except Exception as exc:
            logger.warning("Intent extraction failed for thread %s: %s", thread_id, exc)

    async def handle_message(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        profile: dict | None = None,
        user_payload: dict | None = None,
        attachment_ids: list[str] | None = None,
        display_content: str | None = None,
        offer_ref: str | None = None,
    ) -> tuple[ChatMessage, AgentResponse]:
        """Flux non-streaming : save user msg → orchestrate → save assistant msg → return."""
        bind_chat_context(thread_id=str(thread_id), user_id=str(user_id))
        thread, ctx = self._prepare_context(
            thread_id, user_id, content, profile, user_payload, attachment_ids,
            display_content=display_content, offer_ref=offer_ref,
        )

        llm = get_llm_provider()
        orchestrator = Orchestrator(llm)
        try:
            agent_response = await orchestrator.route(ctx)
        except Exception as exc:
            logger.error("Orchestration failed for thread %s: %s", thread_id, exc)
            agent_response = AgentResponse(
                explanation="Désolé, je n'ai pas pu traiter ta demande. Réessaie dans quelques instants.",
                agent_id="error",
            )

        assistant_msg = self._save_assistant(thread, agent_response, content)
        await self._post_process(thread_id, user_id, llm)
        return assistant_msg, agent_response

    async def stream_message(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        profile: dict | None = None,
        attachment_ids: list[str] | None = None,
    ) -> AsyncIterator[ProgressEvent]:
        """Variante streaming : yield ProgressEvent et sauvegarde à l'event 'done'."""
        bind_chat_context(thread_id=str(thread_id), user_id=str(user_id))
        thread, ctx = self._prepare_context(thread_id, user_id, content, profile, attachment_ids=attachment_ids)

        llm = get_llm_provider()
        orchestrator = Orchestrator(llm)

        agent_response = None
        try:
            async for event in orchestrator.stream_route(ctx):
                if event.type == EventType.done and event.agent_response:
                    agent_response = event.agent_response
                    self._save_assistant(thread, agent_response, content)
                yield event
        except Exception as exc:
            logger.error("Stream orchestration failed for thread %s: %s", thread_id, exc)
            agent_response = AgentResponse(
                explanation="Désolé, je n'ai pas pu traiter ta demande. Réessaie dans quelques instants.",
                agent_id="error",
            )
            self._save_assistant(thread, agent_response, content)
            yield ProgressEvent(
                type=EventType.done,
                agent_id="error",
                agent_response=agent_response,
            )

        if agent_response:
            await self._post_process(thread_id, user_id, llm)

    def edit_message(
        self,
        message_id: uuid.UUID,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        new_content: str,
    ) -> ChatMessage:
        """Édite un message utilisateur : sauvegarde previous_content + cascade les suivants."""
        # Vérifier ownership du thread
        thread = self.repo.get_thread_with_messages(thread_id)
        if not thread or thread.user_id != user_id:
            raise NotFoundError("Thread")

        # Vérifier que le message existe, appartient au thread, est actif et est un message user
        msg = self.repo.get_message(message_id)
        if not msg or msg.thread_id != thread_id or not msg.is_active:
            raise NotFoundError("Message")
        if msg.role != MessageRole.user:
            raise NotFoundError("Message")

        # Sauvegarder l'ancien contenu et mettre à jour
        msg.previous_content = msg.content
        msg.content = new_content
        self.repo.save(msg)

        # Cascade : désactiver tous les messages après celui-ci
        self.repo.cascade_after(thread_id, msg.sequence_number)

        return msg

    def delete_message(
        self,
        message_id: uuid.UUID,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Supprime (soft) un message user + la réponse assistant qui suit (la paire)."""
        # Vérifier ownership du thread
        thread = self.repo.get_thread_with_messages(thread_id)
        if not thread or thread.user_id != user_id:
            raise NotFoundError("Thread")

        # Vérifier que le message existe, appartient au thread et est actif
        msg = self.repo.get_message(message_id)
        if not msg or msg.thread_id != thread_id or not msg.is_active:
            raise NotFoundError("Message")
        if msg.role != MessageRole.user:
            raise NotFoundError("Message")

        # Soft-delete le message user
        self.repo.soft_delete(message_id)

        # Trouver et soft-delete la réponse assistant qui suit (sequence_number + 1)
        active_messages = self.repo.get_active_messages(thread_id)
        for m in active_messages:
            if m.sequence_number == msg.sequence_number + 1 and m.role == MessageRole.assistant:
                self.repo.soft_delete(m.id)
                break

    @staticmethod
    def _enrich_with_offers(
        goal_context: dict,
        goal_type: str | None,
        profile: dict,
        content: str,
        db: "Session",
        offer_ref: str | None = None,
    ) -> dict:
        """Enrichit goal_context avec des offres scrapées pertinentes.

        Si `offer_ref` est fourni, ancre STRICTEMENT sur cette offre précise —
        même s'il y en avait plusieurs dans le fil, seule celle-là est
        transmise, pour que la réponse de l'agent ne porte que sur elle.
        Sinon, recherche générique par intention/pays comme avant.

        Retourne goal_context inchangé si pas d'offres ou intent non pertinent.
        """
        if not goal_type or goal_type in (GoalType.DOCUMENT, GoalType.FREE):
            return goal_context
        try:
            # Savepoint : si la table scraped_offers n'existe pas (ou toute
            # autre erreur SQL), le rollback vers le savepoint remet la
            # transaction dans un état propre au lieu de la « poisonner »
            # (InFailedSqlTransaction PostgreSQL).
            with db.begin_nested():
                svc = ScrapedOfferService(db)
                if offer_ref:
                    one = svc.get_by_ref(offer_ref)
                    offers = [one] if one else []
                else:
                    offers = svc.search_for_agent(
                        intent=goal_type,
                        profile=profile,
                        message=content,
                    )
            if offers:
                return {**goal_context, "relevant_offers": offers}
        except Exception:
            logger.debug("Offer enrichment skipped", exc_info=True)
        return goal_context

    @staticmethod
    def _enrich_with_careers(
        goal_context: dict,
        goal_type: str | None,
        profile: dict,
        content: str,
        db: "Session",
    ) -> dict:
        """Enrichit goal_context avec des fiches métiers du référentiel curaté.

        Chemin séparé de _enrich_with_offers (pas réutilisable tel quel : le
        référentiel métiers n'est pas un ScrapedOfferType) — porte stricte sur
        le seul goal_type orientation, contrairement à la liste d'exclusion
        des offres qui couvre tous les autres goal_types.
        """
        if goal_type != GoalType.ORIENTATION:
            return goal_context
        try:
            with db.begin_nested():
                careers = CareerReferenceService(db).search_for_agent(
                    profile=profile, message=content,
                )
            if careers:
                return {**goal_context, "relevant_careers": careers}
        except Exception:
            logger.debug("Career enrichment skipped", exc_info=True)
        return goal_context

    def _extract_last_agent_id(self, thread_id: uuid.UUID) -> str | None:
        """Extrait le dernier agent_id depuis le payload du dernier message assistant."""
        from sqlalchemy import select
        from app.models.chat import ChatMessage

        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.role == MessageRole.assistant,
                ChatMessage.payload.isnot(None),
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        last_assistant = self.repo.db.execute(stmt).scalar_one_or_none()
        if last_assistant and last_assistant.payload:
            agent_id = last_assistant.payload.get("agent_id")
            # Ne pas persister "free" — laisser le classifier re-essayer
            if agent_id and agent_id != "free":
                return agent_id
        return None


# ── Variante async (asyncpg) ────────────────────────────────────────────────


class AsyncChatService:
    """Variante async de ChatService pour les routers SSE (asyncpg).

    Seuls handle_message() et stream_message() sont exposés — ce sont les
    deux seuls points d'entrée appelés depuis les routers `async def`.

    Services secondaires encore sync (GoalService, PlanService,
    IntentExtractorService) : appelés via asyncio.to_thread() avec une
    session sync propre pour ne pas bloquer la boucle asyncio.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = AsyncChatRepository(db)
        self.memory = AsyncMemoryService(db)

    # ── Helpers internes ──────────────────────────────────────────────────────

    async def _prepare_context(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        profile: dict | None,
        user_payload: dict | None = None,
        attachment_ids: list[str] | None = None,
        display_content: str | None = None,
        offer_ref: str | None = None,
    ) -> tuple[ChatThread, AgentContext]:
        """Charge le thread, injecte la mémoire, persiste le message user.

        display_content : si fourni, stocké en DB à la place de content
            (bulle utilisateur). content reste le contexte complet pour le LLM.
        offer_ref : ancre le tour sur UNE offre précise, cf. `ChatService._prepare_context`.
        """
        thread = await self.repo.get_thread_with_messages(thread_id)
        if not thread or thread.user_id != user_id:
            raise NotFoundError("Thread")

        memory_ctx = await self.memory.get_context(thread_id)
        history = memory_ctx["recent_messages"]
        summary = memory_ctx["summary"]

        stored_content = display_content if display_content else content
        user_msg = await self.repo.add_message(
            thread_id=thread_id, role=MessageRole.user,
            content=stored_content, payload=user_payload,
        )

        # Pièces jointes — exécutées dans un thread (DocumentService est sync)
        enriched_content = content
        image_data: list[dict] = []
        if attachment_ids:
            enriched_content, image_data = await asyncio.to_thread(
                _sync_load_attachments,
                attachment_ids, user_msg.id, user_id, content,
            )

        # goal_type depuis le goal lié ou le dernier agent_id en DB
        goal_type: str | None = None
        goal_type_source: str | None = None
        if thread.goal and thread.goal.type:
            goal_type = thread.goal.type.value
            goal_type_source = "goal"
        else:
            payload = await self.repo.get_last_assistant_payload(thread_id)
            if payload:
                agent_id = payload.get("agent_id")
                if agent_id and agent_id != "free":
                    goal_type = agent_id
                    goal_type_source = "inferred"

        effective_history = history
        if summary:
            effective_history = [
                {"role": "system", "content": f"Résumé de la conversation précédente : {summary}"},
                *history,
            ]

        goal_context: dict = {}
        if thread.goal and thread.goal.context_data:
            goal_context = dict(thread.goal.context_data)

        # Enrichissement offres scrapées — ScrapedOfferService est sync/pgvector
        # → thread isolé avec session sync propre pour ne pas bloquer asyncio
        goal_context = await _enrich_with_offers_async(
            goal_context, goal_type, profile or {}, content, offer_ref=offer_ref,
        )
        goal_context = await _enrich_with_careers_async(
            goal_context, goal_type, profile or {}, content,
        )

        ctx = AgentContext(
            user_id=user_id,
            message=enriched_content,
            history=effective_history,
            profile=profile or {},
            goal_type=goal_type,
            goal_type_source=goal_type_source,
            goal_context=goal_context,
            image_data=image_data,
        )
        return thread, ctx

    async def _save_assistant(
        self,
        thread: ChatThread,
        agent_response: AgentResponse,
        original_content: str,
    ) -> ChatMessage:
        msg = await self.repo.add_message(
            thread_id=thread.id,
            role=MessageRole.assistant,
            content=agent_response.explanation,
            payload=agent_response.model_dump(),
        )
        if not thread.title and original_content:
            thread.title = original_content[:100]
            await self.repo.save(thread)
        return msg

    async def _post_process(
        self, thread_id: uuid.UUID, user_id: uuid.UUID, llm,
    ) -> None:
        """Compression mémoire (async) + extraction d'intention (thread sync)."""
        try:
            await self.memory.maybe_compress(thread_id, llm)
        except Exception as exc:
            logger.warning("Async memory compression failed for thread %s: %s", thread_id, exc)

        # IntentExtractorService est sync — s'exécute dans un thread propre
        try:
            await asyncio.to_thread(
                _sync_extract_intent, thread_id, user_id, llm,
            )
        except Exception as exc:
            logger.warning("Async intent extraction failed for thread %s: %s", thread_id, exc)

    # ── Points d'entrée publics ───────────────────────────────────────────────

    async def handle_message(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        profile: dict | None = None,
        user_payload: dict | None = None,
        attachment_ids: list[str] | None = None,
        display_content: str | None = None,
        offer_ref: str | None = None,
    ) -> tuple[ChatMessage, AgentResponse]:
        bind_chat_context(thread_id=str(thread_id), user_id=str(user_id))
        thread, ctx = await self._prepare_context(
            thread_id, user_id, content, profile, user_payload, attachment_ids,
            display_content=display_content, offer_ref=offer_ref,
        )

        llm = get_llm_provider()
        orchestrator = Orchestrator(llm)
        try:
            agent_response = await orchestrator.route(ctx)
        except Exception as exc:
            logger.error("Async orchestration failed for thread %s: %s", thread_id, exc)
            agent_response = AgentResponse(
                explanation="Désolé, je n'ai pas pu traiter ta demande. Réessaie dans quelques instants.",
                agent_id="error",
            )

        assistant_msg = await self._save_assistant(thread, agent_response, content)
        await self._post_process(thread_id, user_id, llm)
        return assistant_msg, agent_response

    async def stream_message(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        profile: dict | None = None,
        attachment_ids: list[str] | None = None,
        display_content: str | None = None,
        user_payload: dict | None = None,
        offer_ref: str | None = None,
    ) -> AsyncIterator[ProgressEvent]:
        bind_chat_context(thread_id=str(thread_id), user_id=str(user_id))
        thread, ctx = await self._prepare_context(
            thread_id, user_id, content, profile,
            user_payload=user_payload,
            attachment_ids=attachment_ids,
            display_content=display_content,
            offer_ref=offer_ref,
        )

        llm = get_llm_provider()
        orchestrator = Orchestrator(llm)
        agent_response = None

        try:
            async for event in orchestrator.stream_route(ctx):
                if event.type == EventType.done and event.agent_response:
                    agent_response = event.agent_response
                    await self._save_assistant(thread, agent_response, content)
                yield event
        except Exception as exc:
            logger.error("Async stream orchestration failed for thread %s: %s", thread_id, exc)
            agent_response = AgentResponse(
                explanation="Désolé, je n'ai pas pu traiter ta demande. Réessaie dans quelques instants.",
                agent_id="error",
            )
            await self._save_assistant(thread, agent_response, content)
            yield ProgressEvent(
                type=EventType.done,
                agent_id="error",
                agent_response=agent_response,
            )

        if agent_response:
            await self._post_process(thread_id, user_id, llm)


# ── Helpers de thread sync (appelés via asyncio.to_thread) ─────────────────


def _sync_load_attachments(
    attachment_ids: list[str],
    message_id: uuid.UUID,
    user_id: uuid.UUID,
    original_content: str,
) -> tuple[str, list[dict]]:
    """Lie les pièces jointes et extrait texte/images. Exécuté dans un thread sync."""
    import base64
    from app.core.database import SessionLocal
    from app.services.document_service import ATTACHMENTS_DIR, DocumentService

    parsed_ids: list[uuid.UUID] = []
    for raw_id in attachment_ids:
        try:
            parsed_ids.append(uuid.UUID(raw_id))
        except ValueError:
            pass

    texts: list[str] = []
    image_data: list[dict] = []
    with SessionLocal() as sync_db:
        doc_svc = DocumentService(sync_db)
        attachments = doc_svc.link_attachments_to_message(parsed_ids, message_id, user_id)
        for att in attachments:
            if att.extracted_text:
                texts.append(f"[Contenu du fichier '{att.filename}' :\n{att.extracted_text}]")
            elif att.content_type.startswith("image/"):
                try:
                    raw_bytes = (ATTACHMENTS_DIR / att.storage_key).read_bytes()
                    image_data.append({
                        "media_type": att.content_type,
                        "data": base64.standard_b64encode(raw_bytes).decode(),
                    })
                except Exception as exc:
                    logger.warning("Failed to load image attachment %s: %s", att.id, exc)

    enriched = "\n\n".join(texts) + "\n\n" + original_content if texts else original_content
    return enriched, image_data


async def _enrich_with_offers_async(
    goal_context: dict,
    goal_type: str | None,
    profile: dict,
    content: str,
    offer_ref: str | None = None,
) -> dict:
    """Enrichit goal_context avec des offres scrapées (ScrapedOfferService sync/pgvector).

    Lance la recherche dans un thread propre avec une session sync isolée
    pour ne pas bloquer la boucle asyncio. `offer_ref` : cf.
    `ChatService._enrich_with_offers` — ancre sur une offre précise.
    """
    if not goal_type or goal_type in ("document", "free"):
        return goal_context

    def _sync_search() -> list[dict]:
        from app.core.database import SessionLocal
        with SessionLocal() as sync_db:
            svc = ScrapedOfferService(sync_db)
            if offer_ref:
                one = svc.get_by_ref(offer_ref)
                return [one] if one else []
            return svc.search_for_agent(intent=goal_type, profile=profile, message=content)

    try:
        offers = await asyncio.to_thread(_sync_search)
        if offers:
            return {**goal_context, "relevant_offers": offers}
    except Exception:
        logger.debug("Async offer enrichment skipped", exc_info=True)
    return goal_context


async def _enrich_with_careers_async(
    goal_context: dict,
    goal_type: str | None,
    profile: dict,
    content: str,
) -> dict:
    """Enrichit goal_context avec des fiches métiers — mirroir de
    _enrich_with_offers_async, cf. ChatService._enrich_with_careers pour le
    pourquoi d'un chemin séparé (porte stricte orientation, pas de liste
    d'exclusion partagée avec les offres)."""
    if goal_type != "orientation":
        return goal_context

    def _sync_search() -> list[dict]:
        from app.core.database import SessionLocal
        with SessionLocal() as sync_db:
            return CareerReferenceService(sync_db).search_for_agent(profile=profile, message=content)

    try:
        careers = await asyncio.to_thread(_sync_search)
        if careers:
            return {**goal_context, "relevant_careers": careers}
    except Exception:
        logger.debug("Async career enrichment skipped", exc_info=True)
    return goal_context


def _sync_extract_intent(
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    llm,
) -> None:
    """Extraction d'intention via IntentExtractorService (sync). Exécuté dans un thread."""
    import asyncio as _asyncio
    from app.core.database import SessionLocal

    with SessionLocal() as sync_db:
        from app.services.intent_extractor import IntentExtractorService
        extractor = IntentExtractorService(sync_db)
        # maybe_extract est async — on crée une boucle locale dans le thread
        _asyncio.run(extractor.maybe_extract(thread_id, user_id, llm))
        # SessionLocal autocommit=False : flush() seul ne suffit pas,
        # le commit explicite est obligatoire sinon la transaction est rollbackée
        # à la fermeture du contexte et l'intention n'est jamais persistée.
        sync_db.commit()
