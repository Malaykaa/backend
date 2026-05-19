"""Service chat — logique métier : save message, orchestrate, save response.

Deux modes :
- handle_message()  : retourne (ChatMessage, AgentResponse) — mode classique
- stream_message()  : yield des ProgressEvent en SSE — mode streaming
"""

from __future__ import annotations

import logging
import uuid
from typing import AsyncIterator

from sqlalchemy.orm import Session

import structlog
from app.core.logging_config import bind_chat_context

logger = structlog.get_logger(__name__)

from app.agents.base import AgentContext, AgentResponse
from app.agents.events import EventType, ProgressEvent
from app.agents.orchestrator import Orchestrator
from app.core.exceptions import NotFoundError
from app.llm import get_llm_provider
from app.models.chat import ChatMessage, ChatThread, MessageRole
from app.repositories.chat_repo import ChatRepository
from app.services.intent_extractor import IntentExtractorService
from app.services.memory_service import MemoryService
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
    ) -> tuple[ChatThread, AgentContext]:
        """Étapes communes à handle_message/stream_message :
        vérif ownership, mémoire, save user msg, construction de l'AgentContext.
        """
        thread = self.repo.get_thread_with_messages(thread_id)
        if not thread or thread.user_id != user_id:
            raise NotFoundError("Thread")

        # Mémoire AVANT d'ajouter le message user (le courant ira dans ctx.message)
        memory_ctx = self.memory.get_context(thread_id)
        history = memory_ctx["recent_messages"]
        summary = memory_ctx["summary"]

        self.repo.add_message(
            thread_id=thread_id, role=MessageRole.user,
            content=content, payload=user_payload,
        )

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
        )

        ctx = AgentContext(
            user_id=user_id,
            message=content,
            history=effective_history,
            profile=profile or {},
            goal_type=goal_type,
            goal_type_source=goal_type_source,
            goal_context=goal_context,
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
    ) -> tuple[ChatMessage, AgentResponse]:
        """Flux non-streaming : save user msg → orchestrate → save assistant msg → return."""
        bind_chat_context(thread_id=str(thread_id), user_id=str(user_id))
        thread, ctx = self._prepare_context(thread_id, user_id, content, profile, user_payload)

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
    ) -> AsyncIterator[ProgressEvent]:
        """Variante streaming : yield ProgressEvent et sauvegarde à l'event 'done'."""
        bind_chat_context(thread_id=str(thread_id), user_id=str(user_id))
        thread, ctx = self._prepare_context(thread_id, user_id, content, profile)

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
    ) -> dict:
        """Enrichit goal_context avec des offres scrapées pertinentes.

        Retourne goal_context inchangé si pas d'offres ou intent non pertinent.
        """
        if not goal_type or goal_type in ("document", "free"):
            return goal_context
        try:
            # Savepoint : si la table scraped_offers n'existe pas (ou toute
            # autre erreur SQL), le rollback vers le savepoint remet la
            # transaction dans un état propre au lieu de la « poisonner »
            # (InFailedSqlTransaction PostgreSQL).
            with db.begin_nested():
                svc = ScrapedOfferService(db)
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
