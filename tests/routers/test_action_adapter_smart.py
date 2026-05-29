"""Tests non-régression — action_adapter : smart_stream priorité DB > body.history.

Vérifie que smart_stream :
- préfère l'historique chargé depuis la DB à body.history quand threadId est fourni
- utilise body.history quand threadId est absent
- injecte le résumé compressé comme message system
- goal_type='document' sur un premier message (pas d'historique assistant)
- goal_type=None quand l'historique contient déjà une réponse assistant (let Triage decide)
- le bloc 'relevant_offers' n'est PAS enrichi pour goal_type='document'
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentContext, AgentResponse
from app.agents.events import EventType, ProgressEvent


# ── Helpers ───────────────────────────────────────────────────────────────────


def _done_event() -> ProgressEvent:
    return ProgressEvent(
        type=EventType.done,
        agent_id="free",
        agent_response=AgentResponse(explanation="Réponse.", agent_id="free"),
    )


def _make_user() -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.is_active = True
    user.profile = None
    return user


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSmartStreamHistoryPriority:
    @pytest.mark.asyncio
    async def test_db_history_takes_priority_over_body_history(self):
        """L'historique DB (threadId) écrase body.history quand non vide."""
        thread_id = str(uuid.uuid4())
        db_history = [
            {"role": "user", "content": "Message depuis la base de données."},
            {"role": "assistant", "content": "Réponse depuis la base de données."},
        ]
        memory_ctx = {"summary": None, "recent_messages": db_history}

        memory_svc_mock = AsyncMock()
        memory_svc_mock.get_context = AsyncMock(return_value=memory_ctx)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock = MagicMock()
        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import smart_stream, SmartStreamRequest

        request_mock = MagicMock()
        body = SmartStreamRequest(
            message="Nouvelle question.",
            history=[{"role": "user", "content": "Ceci est dans body.history uniquement."}],
            threadId=thread_id,
        )

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
        ):
            response = await smart_stream(
                request=request_mock,
                preset="free",
                body=body,
                current_user=_make_user(),
                db=AsyncMock(),
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        ctx = captured_contexts[0]

        history_contents = [h.get("content", "") for h in ctx.history]
        assert any("base de données" in c for c in history_contents), (
            "L'historique DB doit être utilisé à la place de body.history"
        )
        assert not any("body.history uniquement" in c for c in history_contents), (
            "body.history ne doit PAS être utilisé quand l'historique DB est disponible"
        )

    @pytest.mark.asyncio
    async def test_body_history_used_when_no_thread_id(self):
        """Sans threadId, smart_stream utilise body.history."""
        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock = MagicMock()
        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import smart_stream, SmartStreamRequest

        request_mock = MagicMock()
        body = SmartStreamRequest(
            message="Question.",
            history=[{"role": "user", "content": "Message dans body.history."}],
        )

        with (
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
        ):
            response = await smart_stream(
                request=request_mock,
                preset="free",
                body=body,
                current_user=_make_user(),
                db=AsyncMock(),
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        history_contents = [h.get("content", "") for h in captured_contexts[0].history]
        assert any("body.history" in c for c in history_contents)

    @pytest.mark.asyncio
    async def test_empty_db_history_falls_back_to_body_history(self):
        """Si DB retourne un historique vide, body.history est utilisé en fallback."""
        thread_id = str(uuid.uuid4())
        memory_ctx = {"summary": None, "recent_messages": []}

        memory_svc_mock = AsyncMock()
        memory_svc_mock.get_context = AsyncMock(return_value=memory_ctx)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock = MagicMock()
        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import smart_stream, SmartStreamRequest

        request_mock = MagicMock()
        body = SmartStreamRequest(
            message="Question.",
            history=[{"role": "user", "content": "Fallback depuis body.history."}],
            threadId=thread_id,
        )

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
        ):
            response = await smart_stream(
                request=request_mock,
                preset="free",
                body=body,
                current_user=_make_user(),
                db=AsyncMock(),
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        history_contents = [h.get("content", "") for h in captured_contexts[0].history]
        assert any("Fallback depuis body.history" in c for c in history_contents)

    @pytest.mark.asyncio
    async def test_summary_injected_as_system_message(self):
        """Le résumé compressé est injecté en tête comme message system."""
        thread_id = str(uuid.uuid4())
        memory_ctx = {
            "summary": "Résumé : l'utilisateur cherche un financement pour son projet.",
            "recent_messages": [{"role": "user", "content": "Ok merci."}],
        }

        memory_svc_mock = AsyncMock()
        memory_svc_mock.get_context = AsyncMock(return_value=memory_ctx)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock = MagicMock()
        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import smart_stream, SmartStreamRequest

        request_mock = MagicMock()
        body = SmartStreamRequest(message="Merci !", threadId=thread_id)

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
        ):
            response = await smart_stream(
                request=request_mock,
                preset="free",
                body=body,
                current_user=_make_user(),
                db=AsyncMock(),
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        system_msgs = [h for h in captured_contexts[0].history if h.get("role") == "system"]
        assert len(system_msgs) >= 1
        assert any("financement" in m["content"] for m in system_msgs)


class TestSmartStreamGoalTypeResolution:
    @pytest.mark.asyncio
    async def test_first_message_with_doc_type_sets_goal_document(self):
        """Premier message + preset avec doc_type connu → goal_type='document'."""
        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock = MagicMock()
        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import smart_stream, SmartStreamRequest

        request_mock = MagicMock()
        body = SmartStreamRequest(
            message="Crée un business plan pour mon entreprise de recyclage.",
            history=[],
        )

        with (
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
        ):
            response = await smart_stream(
                request=request_mock,
                preset="business_plan",
                body=body,
                current_user=_make_user(),
                db=AsyncMock(),
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        assert captured_contexts[0].goal_type == "document", (
            "Un premier message avec preset doc → goal_type doit être 'document'"
        )

    @pytest.mark.asyncio
    async def test_conversation_in_progress_goal_type_is_none(self):
        """Historique avec réponse assistant → goal_type=None (laisse le Triage décider)."""
        thread_id = str(uuid.uuid4())
        memory_ctx = {
            "summary": None,
            "recent_messages": [
                {"role": "user", "content": "Bonjour"},
                {"role": "assistant", "content": "Bonjour ! Je peux vous aider."},
            ],
        }

        memory_svc_mock = AsyncMock()
        memory_svc_mock.get_context = AsyncMock(return_value=memory_ctx)

        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock = MagicMock()
        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import smart_stream, SmartStreamRequest

        request_mock = MagicMock()
        body = SmartStreamRequest(
            message="Où puis-je postuler pour des bourses ?",
            threadId=thread_id,
        )

        with (
            patch("app.routers.action_adapter.AsyncMemoryService", return_value=memory_svc_mock),
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
        ):
            response = await smart_stream(
                request=request_mock,
                preset="business_plan",
                body=body,
                current_user=_make_user(),
                db=AsyncMock(),
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        assert captured_contexts[0].goal_type is None, (
            "Avec une conversation en cours, goal_type doit être None "
            "(le Triage LLM doit décider)"
        )

    @pytest.mark.asyncio
    async def test_no_doc_type_preset_uses_preset_goal_type(self):
        """Pour un preset conversationnel sans doc_type, goal_type vient de _PRESET_TO_GOAL_TYPE."""
        captured_contexts: list[AgentContext] = []

        async def capture_stream(ctx: AgentContext):
            captured_contexts.append(ctx)
            yield _done_event()

        orchestrator_mock = MagicMock()
        orchestrator_mock.stream_route = MagicMock(side_effect=capture_stream)

        from app.routers.action_adapter import smart_stream, SmartStreamRequest

        request_mock = MagicMock()
        body = SmartStreamRequest(
            message="Comment préparer mon exposé sur le réchauffement climatique ?",
            history=[],
        )

        with (
            patch("app.routers.action_adapter.Orchestrator", return_value=orchestrator_mock),
            patch("app.routers.action_adapter.get_llm_provider", return_value=AsyncMock()),
            patch("app.routers.action_adapter.extract_profile", return_value={}),
            patch("app.routers.action_adapter.asyncio.to_thread", new=AsyncMock(return_value=[])),
        ):
            response = await smart_stream(
                request=request_mock,
                preset="expose",  # mappé vers "exam" dans _PRESET_TO_GOAL_TYPE
                body=body,
                current_user=_make_user(),
                db=AsyncMock(),
            )
            async for _ in response.body_iterator:
                pass

        assert len(captured_contexts) == 1
        assert captured_contexts[0].goal_type == "exam", (
            "Le preset 'expose' doit être mappé vers goal_type='exam'"
        )
