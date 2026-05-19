"""Tests unitaires — Token continuation, Triage, ExecutionEngine, SSE streaming."""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentContext, AgentResponse
from app.agents.events import EventType, ProgressEvent
from app.agents.execution_engine import ExecutionEngine
from app.agents.orchestrator import Orchestrator
from app.agents.triage import PlanDecision, PlannedStep, Triage, TriageResult
from app.llm.continuation import _looks_truncated, complete_with_continuation
from app.llm.mock_provider import MockProvider


# ── Helpers ─────────────────────────────────────────────


def _ctx(message: str, goal_type: str | None = None, goal_type_source: str | None = None) -> AgentContext:
    return AgentContext(user_id=uuid.uuid4(), message=message, goal_type=goal_type, goal_type_source=goal_type_source)


async def _collect_events(ait) -> list[ProgressEvent]:
    """Collecte tous les events d'un async iterator."""
    events = []
    async for event in ait:
        events.append(event)
    return events


# ── Token Continuation ──────────────────────────────────


class TestTokenContinuation:
    def test_looks_truncated_complete_sentence(self):
        assert not _looks_truncated("Voici la réponse complète.")

    def test_looks_truncated_json_complete(self):
        assert not _looks_truncated('{"key": "value"}')

    def test_looks_truncated_short_text(self):
        # Textes courts ne sont jamais considérés tronqués
        assert not _looks_truncated("court")

    def test_looks_truncated_long_without_terminal(self):
        text = "a" * 300  # Pas de ponctuation terminale
        assert _looks_truncated(text)

    def test_looks_truncated_empty(self):
        assert not _looks_truncated("")

    @pytest.mark.asyncio
    async def test_continuation_no_truncation(self):
        """Si la réponse est complète, pas de relance."""
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="Réponse complète.")

        result = await complete_with_continuation(llm, [{"role": "user", "content": "test"}])
        assert result == "Réponse complète."
        assert llm.complete.call_count == 1

    @pytest.mark.asyncio
    async def test_continuation_with_truncation(self):
        """Si la réponse est tronquée, relance automatique."""
        truncated = "a" * 300  # Pas de terminal → tronqué
        complete_text = "Suite et fin."

        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=[truncated, complete_text])

        result = await complete_with_continuation(llm, [{"role": "user", "content": "test"}])
        assert result == truncated + complete_text
        assert llm.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_continuation_max_rounds(self):
        """Ne dépasse pas max_rounds."""
        truncated = "a" * 300

        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=truncated)

        result = await complete_with_continuation(
            llm, [{"role": "user", "content": "test"}], max_rounds=2,
        )
        assert llm.complete.call_count == 2


# ── Triage ──────────────────────────────────────────────


class TestTriage:
    @pytest.mark.asyncio
    async def test_simple_message_returns_direct(self):
        """Un message simple → mode direct."""
        triage = Triage(MockProvider())

        result = await triage.analyze("Comment préparer le bac ?")

        assert result.intent == "exam"
        assert result.mode == "direct"
        assert result.confidence >= 0.6

    @pytest.mark.asyncio
    async def test_bonjour_returns_direct(self):
        """'bonjour' → direct, pas de workflow."""
        triage = Triage(MockProvider())

        result = await triage.analyze("bonjour")

        assert result.mode == "direct"
        assert result.intent == "free"

    @pytest.mark.asyncio
    async def test_complex_message_returns_workflow(self):
        """Un message avec signal de complexité → workflow avec steps."""
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=json.dumps({
            "intent": "scholarship",
            "confidence": 0.9,
            "mode": "workflow",
            "steps": [
                {"agent_type": "scholarship", "task": "Analyser la bourse", "order": 1},
                {"agent_type": "document", "task": "Préparer le CV", "order": 2},
            ],
        }))

        triage = Triage(llm)

        result = await triage.analyze(
            "Aide-moi de A à Z pour décrocher la bourse Eiffel",
        )

        assert result.mode == "workflow"
        assert len(result.steps) == 2
        assert result.steps[0].agent_type == "scholarship"
        assert result.steps[1].agent_type == "document"
        llm.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_parse_error_fallback(self):
        """Si le LLM retourne du garbage → fallback free, direct."""
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="pas du json")

        triage = Triage(llm)

        result = await triage.analyze("Aide-moi complet pour le bac")

        assert result.mode == "direct"
        assert result.intent == "free"
        assert result.confidence == 0.3

    @pytest.mark.asyncio
    async def test_to_plan_decision_direct(self):
        """TriageResult.to_plan_decision() → PlanDecision direct."""
        result = TriageResult(intent="exam", confidence=0.95, mode="direct")
        plan = result.to_plan_decision()
        assert plan.mode == "direct"
        assert plan.agent_type == "exam"

    @pytest.mark.asyncio
    async def test_to_plan_decision_workflow(self):
        """TriageResult.to_plan_decision() → PlanDecision workflow."""
        steps = [PlannedStep(agent_type="scholarship", task="Analyze", order=1)]
        result = TriageResult(intent="scholarship", confidence=0.9, mode="workflow", steps=steps)
        plan = result.to_plan_decision()
        assert plan.mode == "workflow"
        assert len(plan.steps) == 1

    @pytest.mark.asyncio
    async def test_workflow_with_mock_provider(self):
        """Triage avec MockProvider et signal de workflow."""
        triage = Triage(MockProvider())

        result = await triage.analyze("Aide-moi de A à Z pour la bourse Eiffel")

        assert result.mode == "workflow"
        assert len(result.steps) >= 2


# ── ExecutionEngine ─────────────────────────────────────


class TestExecutionEngine:
    def _make_agent(self, agent_id: str, explanation: str) -> MagicMock:
        agent = MagicMock()
        agent.process = AsyncMock(return_value=AgentResponse(
            explanation=explanation,
            agent_id=agent_id,
        ))
        return agent

    @pytest.mark.asyncio
    async def test_direct_mode(self):
        """Mode direct → un seul agent appelé, un seul event done."""
        agent = self._make_agent("exam", "Voici ton plan de révision.")
        engine = ExecutionEngine(
            llm=AsyncMock(),
            get_agent=lambda intent: agent,
        )
        plan = PlanDecision(mode="direct", agent_type="exam")

        events = await _collect_events(engine.execute(plan, _ctx("prépare le bac")))

        assert len(events) == 1
        assert events[0].type == EventType.done
        assert events[0].agent_response.agent_id == "exam"
        agent.process.assert_called_once()

    @pytest.mark.asyncio
    async def test_workflow_mode(self):
        """Mode workflow → progression par étape + synthèse finale."""
        agents = {
            "scholarship": self._make_agent("scholarship", "Critères Eiffel analysés."),
            "document": self._make_agent("document", "CV généré."),
        }

        # Mock LLM pour la synthèse
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="Synthèse : bourse analysée et CV préparé.")

        engine = ExecutionEngine(
            llm=llm,
            get_agent=lambda intent: agents[intent],
        )
        plan = PlanDecision(
            mode="workflow",
            steps=[
                PlannedStep(agent_type="scholarship", task="Analyser la bourse", order=1),
                PlannedStep(agent_type="document", task="Préparer le CV", order=2),
            ],
        )

        events = await _collect_events(engine.execute(plan, _ctx("bourse Eiffel")))

        # planning + (step_start + step_complete) * 2 + done = 6
        types = [e.type for e in events]
        assert types[0] == EventType.planning
        assert types[1] == EventType.step_start
        assert types[2] == EventType.step_complete
        assert types[3] == EventType.step_start
        assert types[4] == EventType.step_complete
        assert types[5] == EventType.done

        # Vérifier que la réponse finale contient la synthèse
        done_event = events[-1]
        assert done_event.agent_response is not None
        assert done_event.agent_response.agent_id == "workflow"

    @pytest.mark.asyncio
    async def test_workflow_passes_context_between_steps(self):
        """Les résultats des étapes précédentes sont passés en contexte."""
        contexts_received = []

        async def capture_process(ctx):
            contexts_received.append(ctx)
            return AgentResponse(explanation="OK", agent_id="test")

        agent = MagicMock()
        agent.process = AsyncMock(side_effect=capture_process)

        engine = ExecutionEngine(
            llm=AsyncMock(complete=AsyncMock(return_value="Synthèse.")),
            get_agent=lambda _: agent,
        )
        plan = PlanDecision(
            mode="workflow",
            steps=[
                PlannedStep(agent_type="a", task="Étape 1", order=1),
                PlannedStep(agent_type="b", task="Étape 2", order=2),
            ],
        )

        await _collect_events(engine.execute(plan, _ctx("test")))

        # La 2e étape doit avoir reçu le contexte de la 1re
        assert len(contexts_received) == 2
        # Le 2e contexte a un message system avec les résultats précédents
        second_ctx = contexts_received[1]
        system_msgs = [h for h in second_ctx.history if h.get("role") == "system"]
        assert any("Résultats des étapes précédentes" in m["content"] for m in system_msgs)

    @pytest.mark.asyncio
    async def test_workflow_step_failure_continues(self):
        """Si une étape plante, les suivantes s'exécutent quand même."""
        ok_agent = MagicMock()
        ok_agent.process = AsyncMock(return_value=AgentResponse(
            explanation="CV généré.", agent_id="document",
        ))

        failing_agent = MagicMock()
        failing_agent.process = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        def get_agent(intent):
            if intent == "scholarship":
                return failing_agent
            return ok_agent

        engine = ExecutionEngine(
            llm=AsyncMock(complete=AsyncMock(return_value="Synthèse partielle.")),
            get_agent=get_agent,
        )
        plan = PlanDecision(
            mode="workflow",
            steps=[
                PlannedStep(agent_type="scholarship", task="Analyser la bourse", order=1),
                PlannedStep(agent_type="document", task="Préparer le CV", order=2),
            ],
        )

        events = await _collect_events(engine.execute(plan, _ctx("bourse Eiffel")))

        # Même structure : planning + (step_start + step_complete) * 2 + done = 6
        types = [e.type for e in events]
        assert types[0] == EventType.planning
        assert types[1] == EventType.step_start
        assert types[2] == EventType.step_complete  # erreur mais step_complete quand même
        assert types[3] == EventType.step_start
        assert types[4] == EventType.step_complete  # 2e étape réussit
        assert types[5] == EventType.done

        # L'étape en erreur a un message d'erreur dans le step_complete
        error_step = events[2]
        assert "[Erreur]" in error_step.content

        # La 2e étape a bien été exécutée
        ok_agent.process.assert_called_once()

        # La synthèse finale est bien émise
        done_event = events[-1]
        assert done_event.agent_response is not None
        assert done_event.agent_response.agent_id == "workflow"

    @pytest.mark.asyncio
    async def test_workflow_step_failure_in_synthesis(self):
        """La synthèse mentionne l'étape en erreur via les résultats partiels."""
        failing_agent = MagicMock()
        failing_agent.process = AsyncMock(side_effect=ValueError("bad json"))

        ok_agent = MagicMock()
        ok_agent.process = AsyncMock(return_value=AgentResponse(
            explanation="Résultat OK.", agent_id="document",
        ))

        def get_agent(intent):
            if intent == "scholarship":
                return failing_agent
            return ok_agent

        engine = ExecutionEngine(
            llm=AsyncMock(complete=AsyncMock(return_value="Synthèse finale.")),
            get_agent=get_agent,
        )
        plan = PlanDecision(
            mode="workflow",
            steps=[
                PlannedStep(agent_type="scholarship", task="Analyser", order=1),
                PlannedStep(agent_type="document", task="Générer", order=2),
            ],
        )

        events = await _collect_events(engine.execute(plan, _ctx("test")))

        done_event = events[-1]
        assert done_event.type == EventType.done
        # La synthèse doit exister même avec une étape en erreur
        assert done_event.agent_response is not None


# ── Orchestrator stream_route ───────────────────────────


class TestOrchestratorStream:
    @pytest.mark.asyncio
    async def test_stream_route_direct_with_mock(self):
        """stream_route avec MockProvider → events terminés par done."""
        llm = MockProvider()
        orch = Orchestrator(llm)

        events = await _collect_events(
            orch.stream_route(_ctx("Je prépare le bac")),
        )

        # Doit finir par un event done
        assert len(events) >= 1
        done = events[-1]
        assert done.type == EventType.done
        assert done.agent_response is not None
        assert done.agent_response.agent_id == "exam"

    @pytest.mark.asyncio
    async def test_stream_route_with_goal_type(self):
        """Si goal_type défini avec source 'goal' → direct, pas de classification."""
        llm = MockProvider()
        orch = Orchestrator(llm)

        events = await _collect_events(
            orch.stream_route(_ctx("aide-moi", goal_type="exam", goal_type_source="goal")),
        )

        assert len(events) == 1
        assert events[0].type == EventType.done
        assert events[0].agent_response.agent_id == "exam"

    @pytest.mark.asyncio
    async def test_stream_route_low_confidence_clarification(self):
        """Confiance < 0.6 → event done avec clarification."""
        llm = AsyncMock()
        # Le classifier retournera du JSON pour les mots-clés non reconnus
        llm.complete = AsyncMock(return_value='{"intent": "free", "confidence": 0.2}')
        orch = Orchestrator(llm)

        events = await _collect_events(
            orch.stream_route(_ctx("xyzzy")),
        )

        assert len(events) == 1
        assert events[0].type == EventType.done
        assert events[0].agent_response.agent_id == "clarification"


# ── ProgressEvent SSE ───────────────────────────────────


class TestProgressEventSSE:
    def test_chunk_event_sse_format(self):
        event = ProgressEvent(type=EventType.chunk, content="Bonjour")
        sse = event.to_sse()
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")
        assert "chunk" in sse

    def test_done_event_sse_format(self):
        event = ProgressEvent(
            type=EventType.done,
            agent_id="exam",
            agent_response=AgentResponse(explanation="test", agent_id="exam"),
        )
        sse = event.to_sse()
        assert sse.startswith("event: done\n")
        assert "agent_response" in sse

    def test_step_start_event_sse_format(self):
        event = ProgressEvent(
            type=EventType.step_start,
            content="Analyse du profil",
            step_index=0,
            total_steps=3,
        )
        sse = event.to_sse()
        assert sse.startswith("event: step_start\n")


# ── Mock Provider stream ────────────────────────────────


class TestMockProviderStream:
    @pytest.mark.asyncio
    async def test_stream_yields_words(self):
        """Le mock stream yield mot par mot."""
        provider = MockProvider()
        chunks = []
        async for chunk in provider.stream(
            [{"role": "user", "content": "prépare le bac"}],
        ):
            chunks.append(chunk)

        # Doit avoir plus d'un chunk (mot par mot)
        assert len(chunks) > 1
        # Reconstitué = même que complete
        full = "".join(chunks)
        expected = await provider.complete(
            [{"role": "user", "content": "prépare le bac"}],
        )
        assert full == expected


# ── B4 : Rerouting intelligent ──────────────────────────


class TestReroutingIntelligent:
    """Tests pour le rerouting intelligent sur changement de sujet (B4)."""

    @pytest.mark.asyncio
    async def test_inferred_goal_reroutes_on_subject_change(self):
        """Thread exam inféré + 'rédige mon CV' → reroute vers DocumentAgent."""
        orch = Orchestrator(MockProvider())
        ctx = _ctx("rédige mon CV", goal_type="exam", goal_type_source="inferred")

        resp = await orch.route(ctx)

        # Le triage détecte "cv" → document avec confiance >= 0.8
        # Comme intent ("document") != goal_type ("exam"), on reroute
        assert resp.agent_id == "document"

    @pytest.mark.asyncio
    async def test_inferred_goal_stays_on_same_subject(self):
        """Thread exam inféré + 'quelles formules pour le bac' → reste sur ExamAgent."""
        orch = Orchestrator(MockProvider())
        ctx = _ctx("quelles formules pour le bac", goal_type="exam", goal_type_source="inferred")

        resp = await orch.route(ctx)

        # Le triage détecte "bac" → exam, même intent que goal_type → pas de rerouting
        assert resp.agent_id == "exam"

    @pytest.mark.asyncio
    async def test_formal_goal_reroutes_via_triage(self):
        """Thread avec goal formel + changement de sujet détecté par triage LLM → reroute."""
        orch = Orchestrator(MockProvider())
        ctx = _ctx("rédige mon CV", goal_type="exam", goal_type_source="goal")

        resp = await orch.route(ctx)

        # Le triage LLM détecte "CV" → document, différent de "exam" → reroute
        assert resp.agent_id == "document"

    @pytest.mark.asyncio
    async def test_formal_goal_stays_when_same_subject(self):
        """Thread avec goal formel + même sujet → reste sur l'agent du goal."""
        orch = Orchestrator(MockProvider())
        ctx = _ctx("quelles formules réviser pour le bac ?", goal_type="exam", goal_type_source="goal")

        resp = await orch.route(ctx)

        # Le triage détecte "bac" → exam, même intent → pas de rerouting
        assert resp.agent_id == "exam"

    @pytest.mark.asyncio
    async def test_stream_inferred_goal_reroutes_on_subject_change(self):
        """stream_route : thread exam inféré + 'rédige mon CV' → reroute."""
        orch = Orchestrator(MockProvider())
        ctx = _ctx("rédige mon CV", goal_type="exam", goal_type_source="inferred")

        events = await _collect_events(orch.stream_route(ctx))

        done = events[-1]
        assert done.type == EventType.done
        assert done.agent_response.agent_id == "document"

    @pytest.mark.asyncio
    async def test_stream_formal_goal_reroutes_via_triage(self):
        """stream_route : goal formel + changement de sujet détecté par triage → reroute."""
        orch = Orchestrator(MockProvider())
        ctx = _ctx("rédige mon CV", goal_type="exam", goal_type_source="goal")

        events = await _collect_events(orch.stream_route(ctx))

        done = events[-1]
        assert done.type == EventType.done
        assert done.agent_response.agent_id == "document"

    @pytest.mark.asyncio
    async def test_no_goal_type_uses_triage_as_before(self):
        """Sans goal_type → triage complet comme avant (pas de régression)."""
        orch = Orchestrator(MockProvider())
        ctx = _ctx("je prépare le bac")

        resp = await orch.route(ctx)

        assert resp.agent_id == "exam"

    @pytest.mark.asyncio
    async def test_goal_type_source_correctly_set_in_context(self):
        """Vérifie que goal_type_source est bien un champ valide de AgentContext."""
        ctx_goal = AgentContext(
            user_id=uuid.uuid4(),
            message="test",
            goal_type="exam",
            goal_type_source="goal",
        )
        assert ctx_goal.goal_type_source == "goal"

        ctx_inferred = AgentContext(
            user_id=uuid.uuid4(),
            message="test",
            goal_type="exam",
            goal_type_source="inferred",
        )
        assert ctx_inferred.goal_type_source == "inferred"

        ctx_none = AgentContext(
            user_id=uuid.uuid4(),
            message="test",
        )
        assert ctx_none.goal_type_source is None


# ── B6 : Error handling in services ─────────────────────


class TestServiceErrorHandling:
    """Tests pour le error handling gracieux quand l'orchestrateur plante (B6)."""

    @pytest.mark.asyncio
    async def test_chat_handle_message_llm_timeout(self):
        """Simuler un timeout LLM dans handle_message → réponse gracieuse."""
        from app.services.chat_service import ChatService

        # Mock du service et de ses dépendances
        service = ChatService.__new__(ChatService)
        service.repo = MagicMock()
        service.memory = MagicMock()
        service.memory.get_context.return_value = {"recent_messages": [], "summary": None}
        service.memory.maybe_compress = AsyncMock()

        # Simuler un thread valide
        thread = MagicMock()
        thread.user_id = uuid.uuid4()
        thread.goal = None
        thread.title = "Test"
        service.repo.get_thread_with_messages.return_value = thread

        # Mock orchestrator qui plante
        with patch("app.services.chat_service.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.route = AsyncMock(side_effect=TimeoutError("LLM timeout"))

            msg, resp = await service.handle_message(
                thread_id=uuid.uuid4(),
                user_id=thread.user_id,
                content="test message",
            )

        # Vérifier réponse gracieuse
        assert resp.agent_id == "error"
        assert "Réessaie" in resp.explanation

        # Vérifier que la réponse d'erreur a été sauvegardée en DB
        service.repo.add_message.assert_called()  # user msg + assistant msg

    @pytest.mark.asyncio
    async def test_chat_stream_message_llm_timeout(self):
        """Simuler un timeout LLM dans stream_message → event done avec erreur."""
        from app.services.chat_service import ChatService

        service = ChatService.__new__(ChatService)
        service.repo = MagicMock()
        service.memory = MagicMock()
        service.memory.get_context.return_value = {"recent_messages": [], "summary": None}
        service.memory.maybe_compress = AsyncMock()

        thread = MagicMock()
        thread.user_id = uuid.uuid4()
        thread.goal = None
        thread.title = "Test"
        service.repo.get_thread_with_messages.return_value = thread

        async def _failing_stream(*args, **kwargs):
            raise RuntimeError("LLM down")
            yield  # noqa: unreachable — needed to make this an async generator

        with patch("app.services.chat_service.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.stream_route = _failing_stream

            events = []
            async for event in service.stream_message(
                thread_id=uuid.uuid4(),
                user_id=thread.user_id,
                content="test message",
            ):
                events.append(event)

        # Doit émettre un event done avec l'erreur
        assert len(events) == 1
        assert events[0].type == EventType.done
        assert events[0].agent_response.agent_id == "error"
        assert "Réessaie" in events[0].agent_response.explanation

        # Le message d'erreur est sauvegardé en DB
        calls = service.repo.add_message.call_args_list
        assert len(calls) == 2  # user msg + error assistant msg

    @pytest.mark.asyncio
    async def test_plan_generate_llm_timeout(self):
        """Simuler un timeout LLM dans plan_service → plan créé avec message d'erreur."""
        from app.services.plan_service import PlanService

        service = PlanService.__new__(PlanService)
        service.plan_repo = MagicMock()
        service.goal_repo = MagicMock()

        goal = MagicMock()
        goal.user_id = uuid.uuid4()
        goal.type.value = "exam"
        goal.context_data = {}
        service.goal_repo.get_by_id.return_value = goal
        service.plan_repo.get_by_goal_id.return_value = None
        service.plan_repo.get_with_steps.return_value = MagicMock()

        with patch("app.services.plan_service.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.route = AsyncMock(side_effect=TimeoutError("LLM timeout"))

            await service.generate_plan(
                goal_id=uuid.uuid4(),
                user_id=goal.user_id,
                answers={"q": "a"},
            )

        # Le plan a quand même été créé (avec le message d'erreur)
        service.plan_repo.create_plan.assert_called_once()
        call_kwargs = service.plan_repo.create_plan.call_args
        assert "Réessaie" in call_kwargs.kwargs.get("explanation", "") or "Réessaie" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_document_generate_llm_timeout(self):
        """Simuler un timeout LLM dans document_service → document créé avec erreur."""
        from app.models.document import DocumentType
        from app.services.document_service import DocumentService

        service = DocumentService.__new__(DocumentService)
        service.repo = MagicMock()
        service.repo.create_document.return_value = MagicMock()

        with patch("app.services.document_service.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.route = AsyncMock(side_effect=ConnectionError("LLM unreachable"))

            await service.generate(
                user_id=uuid.uuid4(),
                doc_type=DocumentType.cv,
            )

        # Le document a quand même été créé
        service.repo.create_document.assert_called_once()
        call_kwargs = service.repo.create_document.call_args.kwargs
        assert "Réessaie" in call_kwargs.get("content", "")
