"""Tests unitaires Jour 13 — Compression mémoire."""

import asyncio
import uuid
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.models.chat import MessageRole
from app.services.memory_service import (
    COMPRESSION_THRESHOLD,
    MIN_MESSAGES_FOR_COMPRESSION,
    MemoryService,
)


# ── Helpers ──────────────────────────────────────────────


def _make_thread(
    message_count: int = 0,
    compressed_summary: str | None = None,
    summary_up_to: int | None = None,
):
    thread = MagicMock()
    thread.id = uuid.uuid4()
    thread.message_count = message_count
    thread.compressed_summary = compressed_summary
    thread.summary_up_to = summary_up_to
    return thread


def _make_message(role: str, content: str, seq: int):
    msg = MagicMock()
    msg.role = MagicMock()
    msg.role.value = role
    msg.content = content
    msg.sequence_number = seq
    msg.is_active = True
    return msg


# ── get_context ──────────────────────────────────────────


class TestGetContextShortConversation:
    def test_returns_all_messages_when_count_below_threshold(self):
        """Conversation courte (< 10 messages) → retourne tout, pas de résumé."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value

            thread = _make_thread(message_count=5)
            mock_repo.get_by_id.return_value = thread

            messages = [
                _make_message("user", "Bonjour", 1),
                _make_message("assistant", "Salut !", 2),
                _make_message("user", "Aide-moi", 3),
            ]
            mock_repo.get_active_messages.return_value = messages

            service = MemoryService(MagicMock())
            ctx = service.get_context(thread.id)

        assert ctx["summary"] is None
        assert len(ctx["recent_messages"]) == 3
        assert ctx["recent_messages"][0]["role"] == "user"
        assert ctx["recent_messages"][0]["content"] == "Bonjour"

    def test_returns_empty_for_nonexistent_thread(self):
        """Thread inexistant → contexte vide."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_id.return_value = None

            service = MemoryService(MagicMock())
            ctx = service.get_context(uuid.uuid4())

        assert ctx["summary"] is None
        assert ctx["recent_messages"] == []


class TestGetContextLongConversation:
    def test_returns_summary_plus_recent_messages(self):
        """Conversation longue (>= 10 messages) → résumé + messages récents."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value

            thread = _make_thread(
                message_count=15,
                compressed_summary="L'utilisateur prépare le bac S en sciences.",
                summary_up_to=10,
            )
            mock_repo.get_by_id.return_value = thread

            recent = [
                _make_message("user", "Et la physique ?", 11),
                _make_message("assistant", "Voici un plan physique.", 12),
            ]
            mock_repo.get_messages_after.return_value = recent

            service = MemoryService(MagicMock())
            ctx = service.get_context(thread.id)

        assert ctx["summary"] == "L'utilisateur prépare le bac S en sciences."
        assert len(ctx["recent_messages"]) == 2
        mock_repo.get_messages_after.assert_called_once_with(thread.id, 10)

    def test_no_summary_yet_returns_all_recent(self):
        """Conversation longue sans résumé encore → summary_up_to=0, retourne tout après 0."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value

            thread = _make_thread(
                message_count=12,
                compressed_summary=None,
                summary_up_to=None,
            )
            mock_repo.get_by_id.return_value = thread

            messages = [_make_message("user", f"msg {i}", i) for i in range(1, 13)]
            mock_repo.get_messages_after.return_value = messages

            service = MemoryService(MagicMock())
            ctx = service.get_context(thread.id)

        assert ctx["summary"] is None
        assert len(ctx["recent_messages"]) == 12
        mock_repo.get_messages_after.assert_called_once_with(thread.id, 0)


# ── maybe_compress ───────────────────────────────────────


class TestMaybeCompress:
    def test_no_compression_below_min_messages(self):
        """Pas de compression si message_count < MIN_MESSAGES_FOR_COMPRESSION."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(message_count=5)
            mock_repo.get_by_id.return_value = thread

            llm = AsyncMock()
            service = MemoryService(MagicMock())
            result = asyncio.get_event_loop().run_until_complete(
                service.maybe_compress(thread.id, llm)
            )

        assert result is False
        llm.complete.assert_not_called()

    def test_no_compression_below_threshold(self):
        """Pas de compression si les nouveaux messages < seuil."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            thread = _make_thread(message_count=12, summary_up_to=10)
            mock_repo.get_by_id.return_value = thread

            llm = AsyncMock()
            service = MemoryService(MagicMock())
            result = asyncio.get_event_loop().run_until_complete(
                service.maybe_compress(thread.id, llm)
            )

        assert result is False
        llm.complete.assert_not_called()

    def test_compression_triggers_when_threshold_reached(self):
        """Compression se déclenche quand assez de nouveaux messages."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value

            thread = _make_thread(
                message_count=16,
                compressed_summary=None,
                summary_up_to=0,
            )
            mock_repo.get_by_id.return_value = thread

            messages = [
                _make_message("user", "Question 1", 1),
                _make_message("assistant", "Réponse 1", 2),
                _make_message("user", "Question 2", 3),
                _make_message("assistant", "Réponse 2", 4),
                _make_message("user", "Question 3", 5),
                _make_message("assistant", "Réponse 3", 6),
            ]
            mock_repo.get_messages_in_range.return_value = messages

            llm = AsyncMock()
            llm.complete.return_value = "L'utilisateur a posé 3 questions et reçu 3 réponses."

            service = MemoryService(MagicMock())
            result = asyncio.get_event_loop().run_until_complete(
                service.maybe_compress(thread.id, llm)
            )

        assert result is True
        llm.complete.assert_called_once()
        assert thread.compressed_summary == "L'utilisateur a posé 3 questions et reçu 3 réponses."
        assert thread.summary_up_to == COMPRESSION_THRESHOLD
        mock_repo.save.assert_called_once_with(thread)

    def test_compression_with_existing_summary(self):
        """Compression incrémentale : intègre le résumé précédent."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value

            thread = _make_thread(
                message_count=20,
                compressed_summary="Ancien résumé de la conversation.",
                summary_up_to=6,
            )
            mock_repo.get_by_id.return_value = thread

            messages = [
                _make_message("user", "Nouvelle question", 7),
                _make_message("assistant", "Nouvelle réponse", 8),
                _make_message("user", "Encore une", 9),
                _make_message("assistant", "Encore réponse", 10),
                _make_message("user", "Dernière", 11),
                _make_message("assistant", "Fin", 12),
            ]
            mock_repo.get_messages_in_range.return_value = messages

            llm = AsyncMock()
            llm.complete.return_value = "Résumé mis à jour avec les nouvelles questions."

            service = MemoryService(MagicMock())
            result = asyncio.get_event_loop().run_until_complete(
                service.maybe_compress(thread.id, llm)
            )

        assert result is True
        # Vérifier que le prompt contient l'ancien résumé
        call_args = llm.complete.call_args[0][0]
        user_msg = call_args[1]["content"]
        assert "Ancien résumé de la conversation." in user_msg
        assert thread.summary_up_to == 12
        assert thread.compressed_summary == "Résumé mis à jour avec les nouvelles questions."

    def test_no_compression_for_nonexistent_thread(self):
        """Thread inexistant → pas de compression."""
        with patch("app.services.memory_service.ChatRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_id.return_value = None

            llm = AsyncMock()
            service = MemoryService(MagicMock())
            result = asyncio.get_event_loop().run_until_complete(
                service.maybe_compress(uuid.uuid4(), llm)
            )

        assert result is False
