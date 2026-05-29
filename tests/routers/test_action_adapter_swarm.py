"""Tests non-régression — action_adapter : swarm_stream charge l'historique depuis la DB.

Vérifie que swarm_stream :
- appelle AsyncMemoryService.get_context() avec le threadId fourni
- injecte les messages récents de la DB dans AgentContext.history
- injecte le résumé compressé comme message system dans AgentContext.history
- ignore body.threadId invalide (UUID malformé)
- démarre avec un historique vide quand threadId est absent
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentContext, AgentResponse
from app.agents.events import EventType, ProgressEvent


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_sse_event(**kwargs) -> str:
    return f"data: {json.dumps(kwargs)}\n\n"


def _done_event(explanation: str = "Done.") -> ProgressEvent:
    return ProgressEvent(
        type=EventType.done,
        agent_id="document",
        agent_response=AgentResponse(
            explanation=explanation,
            agent_id="document",
            deliverables=["Contenu du document généré."],
        ),
    )


async def _stream_done_event(ctx: AgentContext):
    yield _done_event()


# ── Fixture d'environnement mocké ─────────────────────────────────────────────


def _make_patches(memory_ctx: dict, thread_id: str | None = None):
    """Retourne les patches nécessaires pour isoler swarm_stream."""
    db_mock = AsyncMock()
    user_mock = MagicMock()
    user_mock.id = uuid.uuid4()
    user_mock.is_active = True
    user_mock.profile = None

    memory_service_mock = AsyncMock()
    memory_service_mock.get_context = AsyncMock(return_value=memory_ctx)

    orchestrator_mock = MagicMock()
    orchestrator_mock.stream_route = MagicMock(side_effect=_stream_done_event)

    return db_mock, user_mock, memory_service_mock, orchestrator_mock


# ── Tests ────────────────────────────────────────────────────────────────────


class TestSwarmStreamHistoryLoading:
    @pytest.mark.asyncio
    async def test_history_loaded_from_db_recent_messages(self):
        """swarm_stream charge les messages récents depuis AsyncMemoryService.get_context."""
        thread_id = str(uuid.uuid4())
        memory_ctx = {
            "summary": None,
            "recent_messages": [
                {"role": "user", "content": "Mon entreprise est dans l'agro."},
                {"role": "assistant", "content": "Je prends note."},
            ],
        }
        db_mock, user_mock, memory_svc_mock, orchestrator_mock = _make_patches(memory_ctx, thread_id)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import swarm_stream, SwarmStreamRequest

        request_mock = MagicMock()
        request_mock.state = MagicMock()
        body = SwarmStreamRequest(
            customInstructions="Génère un business plan complet.",
            threadId=thread_id,
        )

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
            patch("app.routers.action_adapter.asyncio.to_thread", new=AsyncMock(return_value=None)),
        ):
            response = await swarm_stream(
                request=request_mock,
                action_type="business_plan",
                body=body,
                current_user=user_mock,
                db=db_mock,
            )

            # Consommer le StreamingResponse pour déclencher le générateur
            content = b""
            async for chunk in response.body_iterator:
                content += chunk if isinstance(chunk, bytes) else chunk.encode()

        memory_svc_mock.get_context.assert_called_once()
        assert len(captured_contexts) == 1
        ctx = captured_contexts[0]

        user_msgs = [h for h in ctx.history if h.get("role") == "user"]
        assert any("agro" in m["content"] for m in user_msgs), (
            "Le message utilisateur DB doit être dans AgentContext.history"
        )

    @pytest.mark.asyncio
    async def test_compressed_summary_injected_as_system_message(self):
        """Le résumé compressé est injecté en tête de l'historique comme message system."""
        thread_id = str(uuid.uuid4())
        memory_ctx = {
            "summary": "L'utilisateur travaille dans le BTP avec 5 employés.",
            "recent_messages": [
                {"role": "user", "content": "Budget : 100k€."},
            ],
        }
        db_mock, user_mock, memory_svc_mock, orchestrator_mock = _make_patches(memory_ctx, thread_id)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import swarm_stream, SwarmStreamRequest

        request_mock = MagicMock()
        body = SwarmStreamRequest(threadId=thread_id)

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
            patch("app.routers.action_adapter.asyncio.to_thread", new=AsyncMock(return_value=None)),
        ):
            response = await swarm_stream(
                request=request_mock,
                action_type="report",
                body=body,
                current_user=user_mock,
                db=db_mock,
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        ctx = captured_contexts[0]

        system_messages = [h for h in ctx.history if h.get("role") == "system"]
        assert len(system_messages) >= 1, "Le résumé doit être injecté comme message system"
        assert any("BTP" in m["content"] for m in system_messages), (
            "Le résumé compressé doit apparaître dans le message system"
        )

    @pytest.mark.asyncio
    async def test_no_thread_id_gives_empty_history(self):
        """Sans threadId, swarm_stream démarre avec un historique vide."""
        memory_ctx = {"summary": None, "recent_messages": []}
        db_mock, user_mock, memory_svc_mock, orchestrator_mock = _make_patches(memory_ctx)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import swarm_stream, SwarmStreamRequest

        request_mock = MagicMock()
        body = SwarmStreamRequest(customInstructions="Génère un CV.")

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
        ):
            response = await swarm_stream(
                request=request_mock,
                action_type="cv",
                body=body,
                current_user=user_mock,
                db=db_mock,
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        assert captured_contexts[0].history == [], (
            "Sans threadId, l'historique doit être vide"
        )

    @pytest.mark.asyncio
    async def test_invalid_thread_id_gives_empty_history(self):
        """Un threadId UUID malformé est ignoré → historique vide."""
        memory_ctx = {"summary": None, "recent_messages": []}
        db_mock, user_mock, memory_svc_mock, orchestrator_mock = _make_patches(memory_ctx)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import swarm_stream, SwarmStreamRequest

        request_mock = MagicMock()
        body = SwarmStreamRequest(threadId="not-a-valid-uuid")

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
        ):
            response = await swarm_stream(
                request=request_mock,
                action_type="cv",
                body=body,
                current_user=user_mock,
                db=db_mock,
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        assert captured_contexts[0].history == [], (
            "Un UUID malformé doit être ignoré et l'historique doit rester vide"
        )
        memory_svc_mock.get_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_goal_type_is_document(self):
        """swarm_stream force toujours goal_type='document'."""
        thread_id = str(uuid.uuid4())
        memory_ctx = {"summary": None, "recent_messages": []}
        db_mock, user_mock, memory_svc_mock, orchestrator_mock = _make_patches(memory_ctx, thread_id)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import swarm_stream, SwarmStreamRequest

        request_mock = MagicMock()
        body = SwarmStreamRequest(threadId=thread_id)

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
            patch("app.routers.action_adapter.asyncio.to_thread", new=AsyncMock(return_value=None)),
        ):
            response = await swarm_stream(
                request=request_mock,
                action_type="business_plan",
                body=body,
                current_user=user_mock,
                db=db_mock,
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        assert captured_contexts[0].goal_type == "document"
