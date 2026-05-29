"""Tests non-régression — DocumentAgent : détection de suivi (_is_follow_up).

Vérifie que _is_follow_up() :
- retourne False quand l'historique n'a pas de réponse assistant (fast-path)
- retourne False sur un signal explicite de format (fast-path)
- retourne True / False selon la réponse du LLM (slow-path)
- retourne True en cas d'échec LLM (fallback sécurisé)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.agents.base import AgentContext
from app.agents.document_agent import _is_follow_up


# ── Helper ───────────────────────────────────────────────────────────────────


def _ctx(
    message: str = "Modifie la section 2",
    history: list[dict] | None = None,
    goal_context: dict | None = None,
) -> AgentContext:
    return AgentContext(
        user_id=uuid.uuid4(),
        message=message,
        history=history or [],
        goal_context=goal_context or {},
    )


# ── Fast-paths sans appel LLM ─────────────────────────────────────────────


class TestIsFollowUpFastPaths:
    @pytest.mark.asyncio
    async def test_no_history_returns_false(self):
        """Sans historique, c'est forcément une nouvelle demande — pas d'appel LLM."""
        llm = AsyncMock()
        ctx = _ctx(history=[])

        result = await _is_follow_up(ctx, llm)

        assert result is False
        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_user_messages_returns_false(self):
        """Historique sans réponse assistant → nouvelle demande, pas d'appel LLM."""
        llm = AsyncMock()
        ctx = _ctx(history=[{"role": "user", "content": "Bonjour"}])

        result = await _is_follow_up(ctx, llm)

        assert result is False
        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_explicit_pdf_signal_returns_false(self):
        """Signal explicite 'en pdf' → nouvelle demande (export), pas d'appel LLM."""
        llm = AsyncMock()
        history = [{"role": "assistant", "content": "Voici ton CV."}]
        ctx = _ctx(message="Donne-moi ça en pdf", history=history)

        result = await _is_follow_up(ctx, llm)

        assert result is False
        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_mets_en_forme_signal_returns_false(self):
        """Signal explicite 'mets en forme' → nouvelle demande, pas d'appel LLM."""
        llm = AsyncMock()
        history = [{"role": "assistant", "content": "Voici ton plan."}]
        ctx = _ctx(message="Mets en forme ce document", history=history)

        result = await _is_follow_up(ctx, llm)

        assert result is False
        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_cree_un_document_signal_returns_false(self):
        """Signal 'crée un document' → nouvelle génération, pas d'appel LLM."""
        llm = AsyncMock()
        history = [{"role": "assistant", "content": "Voici le résumé."}]
        ctx = _ctx(message="Crée un document à partir de ça", history=history)

        result = await _is_follow_up(ctx, llm)

        assert result is False
        llm.complete.assert_not_called()


# ── Slow-path : classification LLM ───────────────────────────────────────


class TestIsFollowUpLLMPath:
    @pytest.mark.asyncio
    async def test_llm_followup_response_returns_true(self):
        """Réponse LLM 'FOLLOWUP' → True."""
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="FOLLOWUP")

        history = [{"role": "assistant", "content": "Voici ton business plan."}]
        ctx = _ctx(message="Raccourcis la section Executive Summary", history=history)

        result = await _is_follow_up(ctx, llm)

        assert result is True
        llm.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_new_response_returns_false(self):
        """Réponse LLM 'NEW' → False."""
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="NEW")

        history = [{"role": "assistant", "content": "Voici ton CV."}]
        ctx = _ctx(message="Fais-moi un business plan pour une startup.", history=history)

        result = await _is_follow_up(ctx, llm)

        assert result is False

    @pytest.mark.asyncio
    async def test_llm_response_case_insensitive(self):
        """Détection insensible à la casse — 'followup' doit matcher."""
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="followup")

        history = [{"role": "assistant", "content": "Voici le rapport."}]
        ctx = _ctx(message="Améliore le résumé", history=history)

        result = await _is_follow_up(ctx, llm)

        assert result is True

    @pytest.mark.asyncio
    async def test_llm_response_with_whitespace(self):
        """Le LLM peut retourner 'FOLLOWUP\\n' — doit quand même matcher."""
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="  FOLLOWUP\n")

        history = [{"role": "assistant", "content": "Voici le contrat."}]
        ctx = _ctx(message="Change la clause 3", history=history)

        result = await _is_follow_up(ctx, llm)

        assert result is True

    @pytest.mark.asyncio
    async def test_llm_failure_returns_true_fallback(self):
        """Si le LLM lève une exception → fallback sécurisé : retourne True (pas de régénération)."""
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=Exception("timeout LLM"))

        history = [{"role": "assistant", "content": "Voici ton document."}]
        ctx = _ctx(message="Modifie quelque chose", history=history)

        result = await _is_follow_up(ctx, llm)

        assert result is True

    @pytest.mark.asyncio
    async def test_goal_context_doc_type_used_in_prompt(self):
        """Le doc_type du goal_context est passé au LLM dans le prompt."""
        captured_messages: list[list[dict]] = []

        llm = AsyncMock()

        async def fake_complete(messages, **kwargs):
            captured_messages.append(messages)
            return "FOLLOWUP"

        llm.complete = AsyncMock(side_effect=fake_complete)

        history = [{"role": "assistant", "content": "Voici le document."}]
        ctx = _ctx(
            message="Refais l'intro",
            history=history,
            goal_context={"document_type": "business_plan"},
        )

        await _is_follow_up(ctx, llm)

        assert len(captured_messages) == 1
        user_msg = next(m for m in captured_messages[0] if m["role"] == "user")
        assert "business_plan" in user_msg["content"]

    @pytest.mark.asyncio
    async def test_called_only_when_assistant_in_history(self):
        """Le LLM n'est appelé QUE si l'historique contient une réponse assistant."""
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="FOLLOWUP")

        # Historique avec uniquement des messages user → pas d'appel LLM
        history_no_assistant = [
            {"role": "user", "content": "Msg 1"},
            {"role": "user", "content": "Msg 2"},
        ]
        ctx = _ctx(history=history_no_assistant)
        await _is_follow_up(ctx, llm)
        llm.complete.assert_not_called()

        # Même message mais avec un assistant → appel LLM
        history_with_assistant = [
            {"role": "user", "content": "Msg 1"},
            {"role": "assistant", "content": "Réponse"},
        ]
        ctx2 = _ctx(history=history_with_assistant)
        await _is_follow_up(ctx2, llm)
        llm.complete.assert_called_once()
