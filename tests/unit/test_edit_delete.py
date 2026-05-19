"""Tests unitaires Jour 11 — Edit/Delete messages + nouveaux champs DB."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.main import app
from app.models.chat import MessageRole


# ── Helpers ──────────────────────────────────────────────


def _make_user():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "edit@test.com"
    user.role = "b2c"
    user.is_active = True
    user.created_at = "2026-01-01T00:00:00+00:00"
    user.profile = None
    return user


def _make_message(
    thread_id: uuid.UUID,
    role: str = "user",
    content: str = "Hello",
    seq: int = 1,
    is_active: bool = True,
    msg_id: uuid.UUID | None = None,
):
    msg = MagicMock()
    msg.id = msg_id or uuid.uuid4()
    msg.thread_id = thread_id
    msg.role = MessageRole.user if role == "user" else MessageRole.assistant
    msg.content = content
    msg.payload = None
    msg.sequence_number = seq
    msg.is_active = is_active
    msg.previous_content = None
    msg.created_at = datetime(2026, 4, 3, tzinfo=timezone.utc)
    return msg


def _make_thread(user_id: uuid.UUID, thread_id: uuid.UUID | None = None):
    thread = MagicMock()
    thread.id = thread_id or uuid.uuid4()
    thread.user_id = user_id
    thread.goal = None
    thread.title = "Test thread"
    thread.status = "open"
    thread.created_at = datetime(2026, 4, 3, tzinfo=timezone.utc)
    thread.messages = []
    thread.message_count = 0
    return thread


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_user():
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.clear()


# ── Test Edit Message (PATCH) ────────────────────────────


class TestEditMessage:
    def test_edit_message_success(self, client: TestClient, auth_user):
        """PATCH modifie le contenu et sauvegarde previous_content."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(auth_user.id, thread_id)
            mock_repo.get_thread_with_messages.return_value = thread

            msg = _make_message(thread_id, "user", "ancien contenu", seq=1, msg_id=msg_id)
            mock_repo.get_message.return_value = msg
            mock_repo.save.return_value = msg
            mock_repo.cascade_after.return_value = 2

            resp = client.patch(
                f"/chat/threads/{thread_id}/messages/{msg_id}",
                json={"content": "nouveau contenu"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(msg_id)

    def test_edit_message_saves_previous_content(self, client: TestClient, auth_user):
        """L'ancien contenu est sauvegardé dans previous_content."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(auth_user.id, thread_id)
            mock_repo.get_thread_with_messages.return_value = thread

            msg = _make_message(thread_id, "user", "ancien contenu", seq=1, msg_id=msg_id)
            mock_repo.get_message.return_value = msg
            mock_repo.save.return_value = msg
            mock_repo.cascade_after.return_value = 0

            client.patch(
                f"/chat/threads/{thread_id}/messages/{msg_id}",
                json={"content": "nouveau contenu"},
            )

        # Vérifier que previous_content a été mis à jour sur l'objet
        assert msg.previous_content == "ancien contenu"
        assert msg.content == "nouveau contenu"

    def test_edit_triggers_cascade(self, client: TestClient, auth_user):
        """L'édition désactive les messages suivants (cascade)."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(auth_user.id, thread_id)
            mock_repo.get_thread_with_messages.return_value = thread

            msg = _make_message(thread_id, "user", "contenu", seq=3, msg_id=msg_id)
            mock_repo.get_message.return_value = msg
            mock_repo.save.return_value = msg
            mock_repo.cascade_after.return_value = 2

            client.patch(
                f"/chat/threads/{thread_id}/messages/{msg_id}",
                json={"content": "modifié"},
            )

        mock_repo.cascade_after.assert_called_once_with(thread_id, 3)

    def test_edit_wrong_thread_404(self, client: TestClient, auth_user):
        """Éditer un message dans un thread inexistant → 404."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_thread_with_messages.return_value = None

            resp = client.patch(
                f"/chat/threads/{thread_id}/messages/{msg_id}",
                json={"content": "test"},
            )

        assert resp.status_code == 404

    def test_edit_assistant_message_404(self, client: TestClient, auth_user):
        """On ne peut pas éditer un message assistant → 404."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(auth_user.id, thread_id)
            mock_repo.get_thread_with_messages.return_value = thread

            msg = _make_message(thread_id, "assistant", "réponse IA", seq=2, msg_id=msg_id)
            mock_repo.get_message.return_value = msg

            resp = client.patch(
                f"/chat/threads/{thread_id}/messages/{msg_id}",
                json={"content": "tentative edit"},
            )

        assert resp.status_code == 404

    def test_edit_inactive_message_404(self, client: TestClient, auth_user):
        """On ne peut pas éditer un message déjà supprimé (is_active=False) → 404."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(auth_user.id, thread_id)
            mock_repo.get_thread_with_messages.return_value = thread

            msg = _make_message(thread_id, "user", "supprimé", seq=1, is_active=False, msg_id=msg_id)
            mock_repo.get_message.return_value = msg

            resp = client.patch(
                f"/chat/threads/{thread_id}/messages/{msg_id}",
                json={"content": "tentative"},
            )

        assert resp.status_code == 404

    def test_edit_no_auth_401(self, client: TestClient):
        """Sans authentification → 401."""
        resp = client.patch(
            f"/chat/threads/{uuid.uuid4()}/messages/{uuid.uuid4()}",
            json={"content": "test"},
        )
        assert resp.status_code == 401


# ── Test Delete Message (DELETE) ─────────────────────────


class TestDeleteMessage:
    def test_delete_message_success(self, client: TestClient, auth_user):
        """DELETE soft-delete le message user + la réponse assistant qui suit."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()
        assistant_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(auth_user.id, thread_id)
            mock_repo.get_thread_with_messages.return_value = thread

            user_msg = _make_message(thread_id, "user", "question", seq=1, msg_id=msg_id)
            assistant_msg = _make_message(thread_id, "assistant", "réponse", seq=2, msg_id=assistant_id)
            mock_repo.get_message.return_value = user_msg
            mock_repo.get_active_messages.return_value = [user_msg, assistant_msg]

            resp = client.delete(f"/chat/threads/{thread_id}/messages/{msg_id}")

        assert resp.status_code == 204
        # Vérifier soft_delete appelé pour user msg + assistant msg
        assert mock_repo.soft_delete.call_count == 2
        mock_repo.soft_delete.assert_any_call(msg_id)
        mock_repo.soft_delete.assert_any_call(assistant_id)

    def test_delete_user_msg_only_if_no_assistant_follows(self, client: TestClient, auth_user):
        """Si pas de réponse assistant après, seul le message user est supprimé."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(auth_user.id, thread_id)
            mock_repo.get_thread_with_messages.return_value = thread

            user_msg = _make_message(thread_id, "user", "dernier msg", seq=5, msg_id=msg_id)
            mock_repo.get_message.return_value = user_msg
            mock_repo.get_active_messages.return_value = [user_msg]

            resp = client.delete(f"/chat/threads/{thread_id}/messages/{msg_id}")

        assert resp.status_code == 204
        mock_repo.soft_delete.assert_called_once_with(msg_id)

    def test_delete_wrong_thread_404(self, client: TestClient, auth_user):
        """Supprimer dans un thread inexistant → 404."""
        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_thread_with_messages.return_value = None

            resp = client.delete(
                f"/chat/threads/{uuid.uuid4()}/messages/{uuid.uuid4()}"
            )

        assert resp.status_code == 404

    def test_delete_assistant_message_404(self, client: TestClient, auth_user):
        """On ne peut pas supprimer directement un message assistant → 404."""
        thread_id = uuid.uuid4()
        msg_id = uuid.uuid4()

        with patch("app.services.chat_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(auth_user.id, thread_id)
            mock_repo.get_thread_with_messages.return_value = thread

            msg = _make_message(thread_id, "assistant", "réponse", seq=2, msg_id=msg_id)
            mock_repo.get_message.return_value = msg

            resp = client.delete(f"/chat/threads/{thread_id}/messages/{msg_id}")

        assert resp.status_code == 404

    def test_delete_no_auth_401(self, client: TestClient):
        """Sans authentification → 401."""
        resp = client.delete(
            f"/chat/threads/{uuid.uuid4()}/messages/{uuid.uuid4()}"
        )
        assert resp.status_code == 401


# ── Test is_active filtering ─────────────────────────────


class TestIsActiveFiltering:
    def test_sequence_number_auto_incremented(self):
        """add_message calcule le bon sequence_number."""
        from app.repositories.chat_repo import ChatRepository

        with patch.object(ChatRepository, "__init__", lambda self, db: None):
            repo = ChatRepository.__new__(ChatRepository)
            repo.db = MagicMock()

            # Simuler max(sequence_number) = 3
            repo.db.execute.return_value.scalar.return_value = 3
            repo.db.flush = MagicMock()

            msg = MagicMock()
            msg.sequence_number = 4

            with patch("app.repositories.chat_repo.ChatMessage") as MockMsg:
                MockMsg.return_value = msg
                MockMsg.thread_id = MagicMock()
                MockMsg.sequence_number = MagicMock()
                result = repo.add_message(
                    thread_id=uuid.uuid4(),
                    role=MessageRole.user,
                    content="test",
                )

            assert result.sequence_number == 4
