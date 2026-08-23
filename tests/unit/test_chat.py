"""Tests unitaires Jour 5 — Chat + ExamAgent end-to-end."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.user import UserRole
from app.agents.base import AgentContext, AgentResponse
from app.agents.exam_agent import ExamAgent
from app.agents.orchestrator import Orchestrator
from app.core.database import get_db
from app.core.deps import extract_profile, get_current_user
from app.core.security import hash_password
from app.llm.mock_provider import MockProvider
from app.main import app
from app.models.chat import ThreadStatus


# ── Helpers ──────────────────────────────────────────────


def _make_user():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "chat@test.com"
    user.role = UserRole.b2c
    user.is_active = True
    user.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    profile = MagicMock()
    profile.first_name = "Alice"
    profile.last_name = "Test"
    profile.gender = "F"
    profile.birth_year = 2000
    profile.country = "Sénégal"
    profile.primary_role = "student"
    profile.domain = "Sciences"
    profile.field_of_study = None
    profile.current_status = "Étudiante"
    profile.preferred_content = None
    profile.skills = None
    profile.interests = None
    profile.self_description = None
    profile.cv_url = None

    user.profile = profile
    return user


def _ctx(message: str, goal_type: str | None = None) -> AgentContext:
    return AgentContext(
        user_id=uuid.uuid4(),
        message=message,
        goal_type=goal_type,
    )


# ── ExamAgent ────────────────────────────────────────────


class TestExamAgent:
    def test_process_returns_agent_response(self):
        llm = MockProvider()
        agent = ExamAgent(llm)
        ctx = _ctx("je prépare le bac")
        response = asyncio.run(agent.process(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "exam"
        assert response.explanation != ""
        assert len(response.steps) > 0

    def test_process_with_profile_context(self):
        llm = MockProvider()
        agent = ExamAgent(llm)
        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="je prépare le bac",
            profile={"first_name": "Alice", "domain": "Sciences"},
        )
        response = asyncio.run(agent.process(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "exam"

    def test_process_with_history(self):
        llm = MockProvider()
        agent = ExamAgent(llm)
        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="les maths et la physique",
            history=[
                {"role": "assistant", "content": "Quel examen prépares-tu ?"},
                {"role": "user", "content": "le bac S"},
                {"role": "assistant", "content": "Quelles matières te posent problème ?"},
            ],
        )
        response = asyncio.run(agent.process(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "exam"

    def test_parse_invalid_json_fallback(self):
        """Si le LLM retourne du texte non-JSON, on fait un fallback propre."""
        agent = ExamAgent(MockProvider())
        result = agent._parse("ceci n'est pas du json")

        assert isinstance(result, AgentResponse)
        assert result.agent_id == "exam"
        assert result.explanation == "ceci n'est pas du json"


# ── extract_profile utility ─────────────────────────────


class TestExtractProfile:
    def test_user_with_profile_returns_dict(self):
        """Un user avec profil retourne les champs attendus."""
        user = _make_user()
        profile = extract_profile(user)
        assert profile == {
            "first_name": "Alice",
            "last_name": "Test",
            "gender": "F",
            "birth_year": 2000,
            "country": "Sénégal",
            "primary_role": "student",
            "domain": "Sciences",
            "field_of_study": None,
            "current_status": "Étudiante",
            "preferred_content": None,
            "skills": None,
            "interests": None,
            "self_description": None,
        }

    def test_user_without_profile_returns_empty(self):
        """Un user sans profil retourne un dict vide."""
        user = MagicMock()
        user.profile = None
        profile = extract_profile(user)
        assert profile == {}


# ── Orchestrator avec ExamAgent enregistré ───────────────


class TestOrchestratorWithExam:
    def test_route_exam_uses_exam_agent(self):
        llm = MockProvider()
        orch = Orchestrator(llm)
        ctx = _ctx("je prépare le bac")
        response = asyncio.run(orch.route(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "exam"
        assert len(response.steps) > 0

    def test_route_exam_explicit_goal_type(self):
        llm = MockProvider()
        orch = Orchestrator(llm)
        ctx = _ctx("aide moi", goal_type="exam")
        response = asyncio.run(orch.route(ctx))

        assert isinstance(response, AgentResponse)
        assert response.agent_id == "exam"


# ── Chat Router (API HTTP) ──────────────────────────────


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_user():
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    # get_db doit aussi être surchargé : la dépendance ouvre une connexion réelle
    # AVANT l'exécution de la route. Les tests qui posent leur propre mock_db le
    # réécrivent ensuite ; ce défaut évite seulement la connexion Postgres.
    app.dependency_overrides[get_db] = lambda: MagicMock()
    yield user
    app.dependency_overrides.clear()


class TestChatRouterCreateThread:
    def test_create_thread(self, client: TestClient, auth_user):
        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = MagicMock()
            thread.id = uuid.uuid4()
            thread.user_id = auth_user.id
            thread.goal_id = None
            thread.goal = None
            thread.title = "Mon thread"
            thread.status = ThreadStatus.open
            thread.created_at = datetime(2026, 4, 2, tzinfo=timezone.utc)
            thread.messages = []
            mock_repo.create_thread.return_value = thread

            resp = client.post("/chat/threads", json={"title": "Mon thread"})

        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Mon thread"
        assert data["status"] == "open"
        assert data["source"] == "99eAnge"
        assert data["presetKey"] is None

    def test_create_thread_no_auth(self, client: TestClient):
        resp = client.post("/chat/threads", json={"title": "test"})
        assert resp.status_code == 401


class TestChatRouterListThreads:
    def test_list_threads(self, client: TestClient, auth_user):
        # L'adapter fait un sa_select direct → on mock la session DB
        t1 = MagicMock()
        t1.id = uuid.uuid4()
        t1.user_id = auth_user.id
        t1.goal = None
        t1.title = "Thread 1"
        t1.status = ThreadStatus.open
        t1.created_at = datetime(2026, 4, 2, tzinfo=timezone.utc)
        t1.messages = []

        mock_db = MagicMock()
        mock_db.execute.return_value.unique.return_value.scalars.return_value.all.return_value = [t1]

        app.dependency_overrides[get_db] = lambda: mock_db
        resp = client.get("/chat/threads")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Thread 1"
        assert data[0]["source"] == "99eAnge"


class TestChatRouterSendMessage:
    def test_send_message_end_to_end(self, client: TestClient, auth_user):
        """Test end-to-end : envoyer 'je prépare le bac' → recevoir réponse format frontend."""
        thread_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo, \
             patch("app.services.chat_service.MemoryService") as MockMemory:
            mock_repo = MockRepo.return_value
            mock_memory = MockMemory.return_value

            # Mock du thread
            thread = MagicMock()
            thread.id = thread_id
            thread.user_id = auth_user.id
            thread.goal = None
            thread.title = None
            thread.messages = []
            mock_repo.get_thread_with_messages.return_value = thread

            # Mock memory_service.get_context
            mock_memory.get_context.return_value = {
                "summary": None,
                "recent_messages": [],
            }
            mock_memory.maybe_compress = AsyncMock(return_value=False)

            # Mock add_message retourne un message
            def make_msg(**kwargs):
                msg = MagicMock()
                msg.id = uuid.uuid4()
                msg.thread_id = thread_id
                msg.role = kwargs.get("role", "assistant")
                msg.content = kwargs.get("content", "")
                msg.payload = kwargs.get("payload")
                msg.sequence_number = 1
                msg.is_active = True
                msg.previous_content = None
                msg.created_at = datetime(2026, 4, 2, tzinfo=timezone.utc)
                return msg

            mock_repo.add_message.side_effect = [
                make_msg(role="user", content="je prépare le bac"),
                make_msg(role="assistant", content="Je vais t'aider"),
            ]
            mock_repo.save.return_value = thread
            mock_repo.db = MagicMock()
            mock_repo.db.execute.return_value.scalar_one_or_none.return_value = None

            resp = client.post(
                f"/chat/threads/{thread_id}/messages",
                json={"content": "je prépare le bac"},
            )

        assert resp.status_code == 200
        data = resp.json()

        # L'adapter retourne FrontendMessageResponse
        assert "content" in data
        assert data["content"] != ""
        assert data["llmUsed"] is True

    def test_send_message_no_auth(self, client: TestClient):
        resp = client.post(
            f"/chat/threads/{uuid.uuid4()}/messages",
            json={"content": "test"},
        )
        assert resp.status_code == 401

    def test_send_empty_message(self, client: TestClient, auth_user):
        resp = client.post(
            f"/chat/threads/{uuid.uuid4()}/messages",
            json={"content": ""},
        )
        assert resp.status_code == 422  # validation Pydantic
