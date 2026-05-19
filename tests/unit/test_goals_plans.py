"""Tests unitaires Jour 7 — Objectifs, Plans et Tracking."""

import asyncio
import uuid
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient

from app.agents.base import AgentResponse
from app.core.deps import get_current_user
from app.main import app
from app.services.goal_service import GoalService


# ── Helpers ──────────────────────────────────────────────


def _make_user():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "goal@test.com"
    user.role = "b2c"
    user.is_active = True
    user.created_at = "2026-01-01T00:00:00+00:00"

    profile = MagicMock()
    profile.first_name = "Alice"
    profile.last_name = "Test"
    profile.country = "Sénégal"
    profile.domain = "Sciences"
    profile.current_status = "Étudiante"
    profile.skills = None
    profile.cv_url = None
    user.profile = profile
    return user


def _make_goal(user_id, goal_type="exam"):
    from app.models.goal import GoalStatus, GoalType

    goal = MagicMock()
    goal.id = uuid.uuid4()
    goal.user_id = user_id
    goal.type = GoalType(goal_type)
    goal.status = GoalStatus.active
    goal.context_data = None
    goal.created_at = "2026-04-02T00:00:00+00:00"
    return goal


def _make_plan(goal_id):
    plan = MagicMock()
    plan.id = uuid.uuid4()
    plan.goal_id = goal_id
    plan.summary = "Plan de révision pour le bac"
    plan.explanation = "Voici un plan structuré pour préparer le bac."
    plan.sources = None
    plan.suggestions = None
    plan.version = 1
    plan.created_at = "2026-04-02T00:00:00+00:00"

    step1 = MagicMock()
    step1.id = uuid.uuid4()
    step1.label = "Analyser le programme"
    step1.description = "Identifier les chapitres clés."
    step1.order = 1
    step1.status = "todo"

    step2 = MagicMock()
    step2.id = uuid.uuid4()
    step2.label = "Planifier les révisions"
    step2.description = "Créer un planning."
    step2.order = 2
    step2.status = "todo"

    plan.steps = [step1, step2]
    return plan


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_user():
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.clear()


# ── GoalService unit tests ──────────────────────────────


class TestGoalServiceDiagnostic:
    def test_diagnostic_questions_exam(self):
        questions = GoalService.get_diagnostic_questions("exam")
        assert len(questions) == 5
        assert any("examen" in q.lower() for q in questions)

    def test_diagnostic_questions_scholarship(self):
        questions = GoalService.get_diagnostic_questions("scholarship")
        assert len(questions) == 5

    def test_diagnostic_questions_funding(self):
        questions = GoalService.get_diagnostic_questions("funding")
        assert len(questions) == 5

    def test_diagnostic_questions_tender(self):
        questions = GoalService.get_diagnostic_questions("tender")
        assert len(questions) == 5

    def test_diagnostic_questions_study_grant(self):
        questions = GoalService.get_diagnostic_questions("study_grant")
        assert len(questions) == 5

    def test_diagnostic_questions_career(self):
        questions = GoalService.get_diagnostic_questions("career")
        assert len(questions) == 5

    def test_diagnostic_questions_unknown(self):
        questions = GoalService.get_diagnostic_questions("unknown")
        assert questions == []


# ── POST /goals ──────────────────────────────────────────


class TestCreateGoal:
    def test_create_goal(self, client: TestClient, auth_user):
        with patch("app.services.goal_service.GoalRepository") as MockGoalRepo, \
             patch("app.services.goal_service.ChatRepository") as MockChatRepo:
            mock_repo = MockGoalRepo.return_value
            goal = _make_goal(auth_user.id)
            mock_repo.create_goal.return_value = goal

            mock_chat_repo = MockChatRepo.return_value
            mock_chat_repo.create_thread.return_value = MagicMock()

            resp = client.post("/goals", json={"type": "exam"})

        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "exam"
        assert data["status"] == "active"

    def test_create_goal_no_auth(self, client: TestClient):
        resp = client.post("/goals", json={"type": "exam"})
        assert resp.status_code == 401

    def test_create_goal_invalid_type(self, client: TestClient, auth_user):
        resp = client.post("/goals", json={"type": "invalid"})
        assert resp.status_code == 422


# ── GET /goals ───────────────────────────────────────────


class TestListGoals:
    def test_list_goals(self, client: TestClient, auth_user):
        with patch("app.services.goal_service.GoalRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            g1 = _make_goal(auth_user.id)
            mock_repo.get_user_goals.return_value = [g1]

            resp = client.get("/goals")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1


# ── GET /goals/{id}/diagnostic ──────────────────────────


class TestDiagnosticQuestions:
    def test_get_diagnostic(self, client: TestClient, auth_user):
        with patch("app.services.goal_service.GoalRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            goal = _make_goal(auth_user.id, "exam")
            mock_repo.get_with_plan.return_value = goal

            resp = client.get(f"/goals/{goal.id}/diagnostic")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 5


# ── POST /goals/{id}/plan ───────────────────────────────


class TestGeneratePlan:
    def test_generate_plan(self, client: TestClient, auth_user):
        goal_id = uuid.uuid4()

        with patch("app.services.plan_service.GoalRepository") as MockGoalRepo, \
             patch("app.services.plan_service.PlanRepository") as MockPlanRepo:

            mock_goal_repo = MockGoalRepo.return_value
            mock_plan_repo = MockPlanRepo.return_value

            goal = _make_goal(auth_user.id, "exam")
            goal.id = goal_id
            goal.context_data = {}
            mock_goal_repo.get_by_id.return_value = goal
            mock_goal_repo.save.return_value = goal

            # Pas de plan existant
            mock_plan_repo.get_by_goal_id.return_value = None

            # Mock create_plan
            plan = _make_plan(goal_id)
            mock_plan_repo.create_plan.return_value = plan
            mock_plan_repo.add_step.return_value = MagicMock()
            mock_plan_repo.get_with_steps.return_value = plan

            resp = client.post(
                f"/plans/goals/{goal_id}",
                json={"answers": {"examen": "bac S", "délai": "3 mois"}},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["summary"] == "Plan de révision pour le bac"
        assert len(data["steps"]) == 2

    def test_generate_plan_no_auth(self, client: TestClient):
        resp = client.post(f"/plans/goals/{uuid.uuid4()}", json={"answers": {}})
        assert resp.status_code == 401


# ── GET /goals/{id}/plan ─────────────────────────────────


class TestGetPlan:
    def test_get_plan(self, client: TestClient, auth_user):
        goal_id = uuid.uuid4()

        with patch("app.services.plan_service.GoalRepository") as MockGoalRepo, \
             patch("app.services.plan_service.PlanRepository") as MockPlanRepo:

            mock_goal_repo = MockGoalRepo.return_value
            mock_plan_repo = MockPlanRepo.return_value

            goal = _make_goal(auth_user.id)
            goal.id = goal_id
            mock_goal_repo.get_by_id.return_value = goal

            plan = _make_plan(goal_id)
            mock_plan_repo.get_by_goal_id.return_value = plan

            resp = client.get(f"/plans/goals/{goal_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["steps"]) == 2

    def test_get_plan_not_found(self, client: TestClient, auth_user):
        goal_id = uuid.uuid4()

        with patch("app.services.plan_service.GoalRepository") as MockGoalRepo, \
             patch("app.services.plan_service.PlanRepository") as MockPlanRepo:

            mock_goal_repo = MockGoalRepo.return_value
            mock_plan_repo = MockPlanRepo.return_value

            goal = _make_goal(auth_user.id)
            goal.id = goal_id
            mock_goal_repo.get_by_id.return_value = goal
            mock_plan_repo.get_by_goal_id.return_value = None

            resp = client.get(f"/plans/goals/{goal_id}")

        assert resp.status_code == 404


# ── PATCH /goals/{id}/steps/{step_id}/complete ──────────


class TestCompleteStep:
    def test_complete_step(self, client: TestClient, auth_user):
        goal_id = uuid.uuid4()
        step_id = uuid.uuid4()
        plan_id = uuid.uuid4()

        with patch("app.services.plan_service.GoalRepository") as MockGoalRepo, \
             patch("app.services.plan_service.PlanRepository") as MockPlanRepo:

            mock_goal_repo = MockGoalRepo.return_value
            mock_plan_repo = MockPlanRepo.return_value

            goal = _make_goal(auth_user.id)
            goal.id = goal_id
            mock_goal_repo.get_by_id.return_value = goal

            plan = MagicMock()
            plan.id = plan_id
            mock_plan_repo.get_by_goal_id.return_value = plan

            step = MagicMock()
            step.id = step_id
            step.plan_id = plan_id
            step.status = "done"
            mock_plan_repo.get_step.return_value = step
            mock_plan_repo.complete_step.return_value = step

            resp = client.patch(f"/goals/{goal_id}/steps/{step_id}/complete")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"

    def test_complete_step_not_found(self, client: TestClient, auth_user):
        goal_id = uuid.uuid4()

        with patch("app.services.plan_service.GoalRepository") as MockGoalRepo, \
             patch("app.services.plan_service.PlanRepository") as MockPlanRepo:

            mock_goal_repo = MockGoalRepo.return_value
            mock_plan_repo = MockPlanRepo.return_value

            goal = _make_goal(auth_user.id)
            goal.id = goal_id
            mock_goal_repo.get_by_id.return_value = goal

            mock_plan_repo.get_by_goal_id.return_value = None

            resp = client.patch(f"/goals/{goal_id}/steps/{uuid.uuid4()}/complete")

        assert resp.status_code == 404
