"""Tests unitaires Jour 6 — 5 agents restants + orchestrator avec tous les agents."""

import asyncio
import uuid

import pytest

from app.agents.base import AgentContext, AgentResponse
from app.agents.career_agent import CareerAgent
from app.agents.funding_agent import FundingAgent
from app.agents.orchestrator import Orchestrator
from app.agents.scholarship_agent import ScholarshipAgent
from app.agents.study_grant_agent import StudyGrantAgent
from app.agents.tender_agent import TenderAgent
from app.llm.mock_provider import MockProvider


# ── Helpers ──────────────────────────────────────────────


def _ctx(message: str, goal_type: str | None = None) -> AgentContext:
    return AgentContext(user_id=uuid.uuid4(), message=message, goal_type=goal_type)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── ScholarshipAgent ────────────────────────────────────


class TestScholarshipAgent:
    def test_process_returns_agent_response(self):
        agent = ScholarshipAgent(MockProvider())
        resp = _run(agent.process(_ctx("je cherche une bourse")))
        assert isinstance(resp, AgentResponse)
        assert resp.agent_id == "scholarship"
        assert resp.explanation != ""
        assert len(resp.steps) > 0

    def test_parse_invalid_json(self):
        agent = ScholarshipAgent(MockProvider())
        result = agent._parse("not json")
        assert result.agent_id == "scholarship"
        assert result.explanation == "not json"


# ── FundingAgent ─────────────────────────────────────────


class TestFundingAgent:
    def test_process_returns_agent_response(self):
        agent = FundingAgent(MockProvider())
        resp = _run(agent.process(_ctx("je cherche un financement")))
        assert isinstance(resp, AgentResponse)
        assert resp.agent_id == "funding"
        assert resp.explanation != ""
        assert len(resp.steps) > 0

    def test_parse_invalid_json(self):
        agent = FundingAgent(MockProvider())
        result = agent._parse("invalid")
        assert result.agent_id == "funding"


# ── TenderAgent ──────────────────────────────────────────


class TestTenderAgent:
    def test_process_returns_agent_response(self):
        agent = TenderAgent(MockProvider())
        resp = _run(agent.process(_ctx("il y a un appel d'offre")))
        assert isinstance(resp, AgentResponse)
        assert resp.agent_id == "tender"
        assert resp.explanation != ""
        assert len(resp.steps) > 0

    def test_parse_invalid_json(self):
        agent = TenderAgent(MockProvider())
        result = agent._parse("bad data")
        assert result.agent_id == "tender"


# ── StudyGrantAgent ──────────────────────────────────────


class TestStudyGrantAgent:
    def test_process_returns_agent_response(self):
        agent = StudyGrantAgent(MockProvider())
        resp = _run(agent.process(_ctx("je veux étudier à l'université")))
        assert isinstance(resp, AgentResponse)
        assert resp.agent_id == "study_grant"
        assert resp.explanation != ""
        assert len(resp.steps) > 0

    def test_parse_invalid_json(self):
        agent = StudyGrantAgent(MockProvider())
        result = agent._parse("nope")
        assert result.agent_id == "study_grant"


# ── CareerAgent ──────────────────────────────────────────


class TestCareerAgent:
    def test_process_returns_agent_response(self):
        agent = CareerAgent(MockProvider())
        resp = _run(agent.process(_ctx("je veux changer de carrière")))
        assert isinstance(resp, AgentResponse)
        assert resp.agent_id == "career"
        assert resp.explanation != ""
        assert len(resp.steps) > 0

    def test_parse_invalid_json(self):
        agent = CareerAgent(MockProvider())
        result = agent._parse("oops")
        assert result.agent_id == "career"


# ── Orchestrator avec tous les agents enregistrés ───────


class TestOrchestratorAllAgents:
    """Vérifie que l'orchestrateur route vers chaque agent spécialisé."""

    def test_route_scholarship(self):
        resp = _run(Orchestrator(MockProvider()).route(_ctx("je cherche une bourse")))
        assert resp.agent_id == "scholarship"
        assert len(resp.steps) > 0

    def test_route_funding(self):
        resp = _run(Orchestrator(MockProvider()).route(_ctx("je cherche un financement")))
        assert resp.agent_id == "funding"
        assert len(resp.steps) > 0

    def test_route_tender(self):
        resp = _run(Orchestrator(MockProvider()).route(_ctx("il y a un appel d'offre")))
        assert resp.agent_id == "tender"
        assert len(resp.steps) > 0

    def test_route_study_grant(self):
        resp = _run(Orchestrator(MockProvider()).route(_ctx("je veux aller à l'université")))
        assert resp.agent_id == "study_grant"
        assert len(resp.steps) > 0

    def test_route_career(self):
        resp = _run(Orchestrator(MockProvider()).route(_ctx("je veux changer de carrière")))
        assert resp.agent_id == "career"
        assert len(resp.steps) > 0

    def test_route_exam_still_works(self):
        resp = _run(Orchestrator(MockProvider()).route(_ctx("je prépare le bac")))
        assert resp.agent_id == "exam"
        assert len(resp.steps) > 0

    def test_route_with_explicit_goal_type(self):
        """goal_type + message cohérent → agent correspondant (triage confirme le sujet)."""
        cases = {
            "exam": "je prépare mon examen de maths",
            "scholarship": "je cherche une bourse d'études",
            "funding": "je cherche un financement pour mon projet",
            "tender": "je veux répondre à un appel d'offres",
            "study_grant": "je veux étudier à l'étranger",
            "career": "je veux changer de carrière",
        }
        for goal_type, message in cases.items():
            resp = _run(Orchestrator(MockProvider()).route(_ctx(message, goal_type=goal_type)))
            assert resp.agent_id == goal_type, f"Expected {goal_type}, got {resp.agent_id} for '{message}'"

    def test_ambiguous_message_returns_clarification(self):
        """Message ambigu sans mots-clés → confiance faible → clarification."""
        resp = _run(Orchestrator(MockProvider()).route(_ctx("bonjour")))
        assert resp.agent_id == "clarification"
        assert len(resp.clarifications) > 0
