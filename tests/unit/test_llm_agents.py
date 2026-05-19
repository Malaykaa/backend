"""Tests unitaires Jour 4 — LLM providers, triage, orchestrator."""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.agents.base import AgentContext, AgentResponse
from app.agents.triage import Triage
from app.agents.orchestrator import Orchestrator
from app.llm import get_llm_provider
from app.llm.mock_provider import MockProvider


# ── Helpers ───────────────────────────────────────────────

def _ctx(message: str, goal_type: str | None = None) -> AgentContext:
    return AgentContext(
        user_id=uuid.uuid4(),
        message=message,
        goal_type=goal_type,
    )


# ── MockProvider ──────────────────────────────────────────

class TestMockProvider:
    def test_complete_returns_json(self):
        provider = MockProvider()
        result = asyncio.get_event_loop().run_until_complete(
            provider.complete([{"role": "user", "content": "je prépare le bac"}])
        )
        data = json.loads(result)
        assert "explanation" in data
        assert "steps" in data
        assert "agent_id" in data
        assert data["agent_id"] == "exam"

    def test_complete_free_message(self):
        provider = MockProvider()
        result = asyncio.get_event_loop().run_until_complete(
            provider.complete([{"role": "user", "content": "bonjour"}])
        )
        data = json.loads(result)
        assert data["agent_id"] == "free"

    def test_complete_scholarship(self):
        provider = MockProvider()
        result = asyncio.get_event_loop().run_until_complete(
            provider.complete([{"role": "user", "content": "je cherche une bourse"}])
        )
        data = json.loads(result)
        assert data["agent_id"] == "scholarship"

    def test_complete_funding(self):
        provider = MockProvider()
        result = asyncio.get_event_loop().run_until_complete(
            provider.complete([{"role": "user", "content": "je cherche un financement"}])
        )
        data = json.loads(result)
        assert data["agent_id"] == "funding"

    def test_complete_document(self):
        provider = MockProvider()
        result = asyncio.get_event_loop().run_until_complete(
            provider.complete([{"role": "user", "content": "génère mon cv"}])
        )
        data = json.loads(result)
        assert data["agent_id"] == "document"

    def test_stream_yields_content(self):
        provider = MockProvider()

        async def _run():
            chunks = []
            async for chunk in provider.stream([{"role": "user", "content": "salut"}]):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.get_event_loop().run_until_complete(_run())
        assert len(chunks) >= 1
        # Reconstituer et vérifier que c'est du JSON valide
        full = "".join(chunks)
        data = json.loads(full)
        assert "explanation" in data


# ── Triage ────────────────────────────────────────────────

class TestTriage:
    def test_exam(self):
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("je prépare le bac")
        )
        assert result.intent == "exam"
        assert result.confidence >= 0.6

    def test_scholarship(self):
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("comment trouver une bourse ?")
        )
        assert result.intent == "scholarship"

    def test_career(self):
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("je veux changer de carrière")
        )
        assert result.intent == "career"

    def test_document(self):
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("génère mon cv")
        )
        assert result.intent == "document"

    def test_funding(self):
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("je cherche un financement pour mon projet")
        )
        assert result.intent == "funding"

    def test_tender(self):
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("il y a un appel d'offre intéressant")
        )
        assert result.intent == "tender"

    def test_ambiguous_returns_free(self):
        """Message sans mots-clés clairs → free avec confiance haute (mock)."""
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("aide-moi s'il te plaît")
        )
        assert result.intent == "free"

    def test_mode_direct_by_default(self):
        """La majorité des messages → mode direct."""
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("je prépare le bac")
        )
        assert result.mode == "direct"

    def test_workflow_signal(self):
        """Demande complexe → mode workflow."""
        triage = Triage(MockProvider())
        result = asyncio.get_event_loop().run_until_complete(
            triage.analyze("Aide-moi de A à Z pour la bourse Eiffel")
        )
        assert result.mode == "workflow"
        assert len(result.steps) >= 2


# ── Orchestrator ──────────────────────────────────────────

class TestOrchestrator:
    def test_route_exam(self):
        llm = MockProvider()
        orch = Orchestrator(llm)
        ctx = _ctx("je prépare le bac")
        response = asyncio.get_event_loop().run_until_complete(orch.route(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "exam"
        assert len(response.steps) > 0
        assert response.explanation != ""

    def test_route_scholarship(self):
        llm = MockProvider()
        orch = Orchestrator(llm)
        ctx = _ctx("je cherche une bourse")
        response = asyncio.get_event_loop().run_until_complete(orch.route(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "scholarship"

    def test_route_with_explicit_goal_type(self):
        """Si goal_type est fourni, le classifier est court-circuité."""
        llm = MockProvider()
        orch = Orchestrator(llm)
        ctx = _ctx("n'importe quoi", goal_type="funding")
        response = asyncio.get_event_loop().run_until_complete(orch.route(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "funding"

    def test_route_ambiguous_returns_clarification(self):
        """Message ambigu sans mots-clés → confiance faible → clarification."""
        llm = MockProvider()
        orch = Orchestrator(llm)
        ctx = _ctx("bonjour comment ça va ?")
        response = asyncio.get_event_loop().run_until_complete(orch.route(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "clarification"
        assert len(response.clarifications) > 0

    def test_response_has_all_fields(self):
        llm = MockProvider()
        orch = Orchestrator(llm)
        ctx = _ctx("je prépare un examen")
        response = asyncio.get_event_loop().run_until_complete(orch.route(ctx))

        # Vérifie que tous les champs du contrat AgentResponse sont présents
        assert hasattr(response, "explanation")
        assert hasattr(response, "steps")
        assert hasattr(response, "suggestions")
        assert hasattr(response, "sources")
        assert hasattr(response, "deliverables")
        assert hasattr(response, "clarifications")
        assert hasattr(response, "agent_id")


# ── Factory get_llm_provider ──────────────────────────────

class TestGetLlmProvider:
    def test_default_is_mock(self):
        """LLM_PROVIDER=mock dans .env → retourne MockProvider."""
        provider = get_llm_provider()
        assert isinstance(provider, MockProvider)
