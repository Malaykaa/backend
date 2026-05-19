"""Tests unitaires Jour 14 — Seuil de confiance + Attachments."""

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.agents.base import AgentContext, AgentResponse
from app.agents.triage import Triage, TriageResult
from app.agents.orchestrator import Orchestrator
from app.core.deps import get_current_user
from app.llm.mock_provider import MockProvider
from app.main import app


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _ctx(message: str, goal_type: str | None = None) -> AgentContext:
    return AgentContext(user_id=uuid.uuid4(), message=message, goal_type=goal_type)


# ── Triage : confiance ───────────────────────────────────


class TestTriageConfidence:
    def test_keyword_match_returns_high_confidence(self):
        """Mots-clés matchés → confiance élevée."""
        triage = Triage(MockProvider())
        result = _run(triage.analyze("je prépare le bac"))
        assert isinstance(result, TriageResult)
        assert result.intent == "exam"
        assert result.confidence >= 0.9

    def test_keyword_bourse_high_confidence(self):
        """Mot-clé 'bourse' → scholarship, confiance élevée."""
        triage = Triage(MockProvider())
        result = _run(triage.analyze("je cherche une bourse"))
        assert result.intent == "scholarship"
        assert result.confidence >= 0.9

    def test_llm_fallback_returns_confidence(self):
        """Sans mot-clé, le LLM est appelé et retourne un score de confiance."""
        llm = AsyncMock()
        llm.complete.return_value = '{"intent": "career", "confidence": 0.85, "mode": "direct"}'
        triage = Triage(llm)
        result = _run(triage.analyze("aidez-moi s'il vous plaît"))
        assert result.intent == "career"
        assert result.confidence == 0.85

    def test_llm_low_confidence(self):
        """LLM avec confiance faible."""
        llm = AsyncMock()
        llm.complete.return_value = '{"intent": "free", "confidence": 0.2, "mode": "direct"}'
        triage = Triage(llm)
        result = _run(triage.analyze("hmm"))
        assert result.intent == "free"
        assert result.confidence == 0.2

    def test_llm_invalid_json_fallback(self):
        """Réponse LLM non-JSON → confiance par défaut basse."""
        llm = AsyncMock()
        llm.complete.return_value = "je ne sais pas"
        triage = Triage(llm)
        result = _run(triage.analyze("???"))
        assert result.intent == "free"
        assert result.confidence == 0.3


# ── Orchestrator : seuil de confiance ────────────────────


class TestOrchestratorConfidenceThreshold:
    def test_high_confidence_routes_to_agent(self):
        """Confiance >= 0.6 → route vers l'agent normalement."""
        resp = _run(Orchestrator(MockProvider()).route(_ctx("je prépare le bac")))
        assert resp.agent_id == "exam"
        assert resp.clarifications == []

    def test_low_confidence_returns_clarification(self):
        """Confiance < 0.6 → retourne une demande de clarification."""
        llm = AsyncMock()
        # Le classifier va retourner une confiance faible
        llm.complete.return_value = '{"intent": "free", "confidence": 0.3}'

        resp = _run(Orchestrator(llm).route(_ctx("bonjour")))
        assert resp.agent_id == "clarification"
        assert "Pouvez-vous préciser votre demande ?" in resp.clarifications

    def test_explicit_goal_type_skips_confidence_check(self):
        """Si goal_type est déjà défini, pas de classification → pas de seuil."""
        resp = _run(Orchestrator(MockProvider()).route(_ctx("aide moi", goal_type="exam")))
        assert resp.agent_id == "exam"


# ── Attachments : model + service ────────────────────────


class TestAttachmentService:
    def test_create_attachment_saves_file(self, tmp_path):
        """create_attachment sauvegarde le fichier et crée l'Attachment en DB."""
        with patch("app.services.document_service.ATTACHMENTS_DIR", tmp_path), \
             patch("app.services.document_service.DocumentRepository"):
            from app.services.document_service import DocumentService

            db = MagicMock()
            db.add = MagicMock()
            db.flush = MagicMock()

            service = DocumentService(db)
            service.repo = MagicMock()
            service.repo.db = db

            msg_id = uuid.uuid4()
            data = b"fake pdf content"

            attachment = service.create_attachment(
                message_id=msg_id,
                filename="document.pdf",
                content_type="application/pdf",
                data=data,
            )

        assert attachment.filename == "document.pdf"
        assert attachment.content_type == "application/pdf"
        assert attachment.size_bytes == len(data)
        assert attachment.message_id == msg_id
        # Vérifie que le fichier a bien été écrit
        saved_file = tmp_path / attachment.storage_key
        assert saved_file.exists()
        assert saved_file.read_bytes() == data


# ── Router files : GET /files/attachments/{id} ───────────


def _make_user():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@test.com"
    user.role = "b2c"
    user.is_active = True
    user.profile = None
    return user


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_user():
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.clear()


class TestFilesRouter:
    def test_download_attachment_success(self, client, auth_user, tmp_path):
        """GET /files/attachments/{id} retourne le fichier."""
        attachment_id = uuid.uuid4()
        storage_key = "abc123_test.pdf"

        # Créer le fichier physique
        (tmp_path / storage_key).write_bytes(b"PDF content here")

        with patch("app.services.document_service.DocumentRepository"), \
             patch("app.services.document_service.ATTACHMENTS_DIR", tmp_path):
            from app.services.document_service import DocumentService

            with patch.object(DocumentService, "get_attachment") as mock_get, \
                 patch.object(DocumentService, "get_attachment_path") as mock_path:

                attachment = MagicMock()
                attachment.id = attachment_id
                attachment.filename = "test.pdf"
                attachment.content_type = "application/pdf"
                attachment.storage_key = storage_key
                attachment.message = MagicMock()
                attachment.message.thread = MagicMock()
                attachment.message.thread.user_id = auth_user.id

                mock_get.return_value = attachment
                mock_path.return_value = tmp_path / storage_key

                resp = client.get(f"/files/attachments/{attachment_id}")

        assert resp.status_code == 200
        assert resp.content == b"PDF content here"

    def test_download_attachment_wrong_user_404(self, client, auth_user):
        """Accéder à un attachment d'un autre utilisateur → 404."""
        attachment_id = uuid.uuid4()

        with patch("app.services.document_service.DocumentRepository"):
            from app.services.document_service import DocumentService

            with patch.object(DocumentService, "get_attachment") as mock_get:
                attachment = MagicMock()
                attachment.message = MagicMock()
                attachment.message.thread = MagicMock()
                attachment.message.thread.user_id = uuid.uuid4()  # autre user

                mock_get.return_value = attachment

                resp = client.get(f"/files/attachments/{attachment_id}")

        assert resp.status_code == 404

    def test_download_attachment_no_auth_401(self, client):
        """Sans authentification → 401."""
        resp = client.get(f"/files/attachments/{uuid.uuid4()}")
        assert resp.status_code == 401
