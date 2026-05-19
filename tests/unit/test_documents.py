"""Tests unitaires Jour 8 — Documents (Actions Rapides) + PDF export."""

import asyncio
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.agents.base import AgentContext, AgentResponse
from app.agents.document_agent import DocumentAgent
from app.agents.orchestrator import Orchestrator
from app.core.deps import get_current_user
from app.main import app
from app.llm.mock_provider import MockProvider
from app.models.document import DocumentType
from app.services.document_service import DocumentService


# ── Helpers ──────────────────────────────────────────────


def _make_user():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "doc@test.com"
    user.role = "b2c"
    user.is_active = True
    user.created_at = "2026-01-01T00:00:00+00:00"

    profile = MagicMock()
    profile.first_name = "Alice"
    profile.last_name = "Diallo"
    profile.country = "Sénégal"
    profile.domain = "Informatique"
    profile.current_status = "Développeuse"
    profile.skills = None
    profile.cv_url = None
    user.profile = profile
    return user


def _make_document(user_id, doc_type="cv"):
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.user_id = user_id
    doc.goal_id = None
    doc.type = DocumentType(doc_type)
    doc.content = "# CV\n\n## Profil\nDéveloppeuse passionnée.\n\n## Compétences\n- Python\n- FastAPI\n- React"
    doc.export_url = None
    doc.version = 1
    doc.created_at = "2026-04-02T00:00:00+00:00"
    return doc


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_user():
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.clear()


# ── DocumentAgent ────────────────────────────────────────


class TestDocumentAgent:
    def test_process_cv(self):
        agent = DocumentAgent(MockProvider())
        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="génère mon cv",
            goal_type="document",
            goal_context={"document_type": "cv"},
        )
        resp = _run(agent.process(ctx))
        assert isinstance(resp, AgentResponse)
        assert resp.agent_id == "document"
        assert resp.explanation != ""

    def test_process_cover_letter(self):
        agent = DocumentAgent(MockProvider())
        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="rédige une lettre de motivation",
            goal_type="document",
            goal_context={"document_type": "cover_letter"},
        )
        resp = _run(agent.process(ctx))
        assert resp.agent_id == "document"

    def test_clarification_short_message(self):
        """Le document agent demande des clarifications si le message est trop court."""
        agent = DocumentAgent(MockProvider())
        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="cv",
            goal_type="document",
            goal_context={"document_type": "cv"},
        )
        resp = _run(agent.process(ctx))
        assert resp.agent_id == "document"
        assert len(resp.clarifications) > 0


# ── Orchestrator routes document ─────────────────────────


class TestOrchestratorDocument:
    def test_route_document(self):
        resp = _run(Orchestrator(MockProvider()).route(
            AgentContext(
                user_id=uuid.uuid4(),
                message="génère mon cv",
                goal_type="document",
                goal_context={"document_type": "cv"},
            )
        ))
        assert resp.agent_id == "document"

    def test_route_document_via_keyword(self):
        resp = _run(Orchestrator(MockProvider()).route(
            AgentContext(user_id=uuid.uuid4(), message="génère mon cv")
        ))
        assert resp.agent_id == "document"


# ── PDF Export ──────────────────────────────────────────


class TestPdfExport:
    def test_export_pdf_returns_bytes(self):
        doc = _make_document(uuid.uuid4(), "cv")
        pdf_bytes = DocumentService.export_pdf(doc)

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        # PDF magic bytes
        assert pdf_bytes[:5] == b"%PDF-"

    def test_export_pdf_cover_letter(self):
        doc = _make_document(uuid.uuid4(), "cover_letter")
        doc.content = "Madame, Monsieur,\n\nJe me permets de vous adresser ma candidature.\n\nCordialement."
        pdf_bytes = DocumentService.export_pdf(doc)
        assert pdf_bytes[:5] == b"%PDF-"

    def test_export_pdf_business_plan(self):
        doc = _make_document(uuid.uuid4(), "business_plan")
        doc.content = "## Résumé\nProjet innovant.\n\n## Marché\n- Cible : PME\n- Taille : 1M"
        pdf_bytes = DocumentService.export_pdf(doc)
        assert pdf_bytes[:5] == b"%PDF-"


# ── Router POST /documents/generate ─────────────────────


class TestGenerateDocument:
    def test_generate_cv(self, client: TestClient, auth_user):
        with patch("app.services.document_service.DocumentRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            doc = _make_document(auth_user.id, "cv")
            mock_repo.create_document.return_value = doc

            resp = client.post("/documents/generate", json={"type": "cv"})

        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "cv"
        assert data["content"] != ""

    def test_generate_no_auth(self, client: TestClient):
        resp = client.post("/documents/generate", json={"type": "cv"})
        assert resp.status_code == 401

    def test_generate_invalid_type(self, client: TestClient, auth_user):
        resp = client.post("/documents/generate", json={"type": "invalid"})
        assert resp.status_code == 422


# ── Router GET /documents/{id}/export ────────────────────


class TestExportDocument:
    def test_export_pdf_endpoint(self, client: TestClient, auth_user):
        doc_id = uuid.uuid4()

        with patch("app.services.document_service.DocumentRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            doc = _make_document(auth_user.id, "cv")
            doc.id = doc_id
            mock_repo.get_by_id.return_value = doc

            resp = client.get(f"/documents/{doc_id}/export")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.content[:5] == b"%PDF-"

    def test_export_not_found(self, client: TestClient, auth_user):
        with patch("app.services.document_service.DocumentRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_id.return_value = None

            resp = client.get(f"/documents/{uuid.uuid4()}/export")

        assert resp.status_code == 404


# ── Router GET /documents ────────────────────────────────


class TestListDocuments:
    def test_list_documents(self, client: TestClient, auth_user):
        with patch("app.services.document_service.DocumentRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            d1 = _make_document(auth_user.id, "cv")
            mock_repo.get_user_documents.return_value = [d1]

            resp = client.get("/documents")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["type"] == "cv"
