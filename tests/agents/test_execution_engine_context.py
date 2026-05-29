"""Tests non-régression — ExecutionEngine : injection du contexte de conversation.

Vérifie que _build_conversation_context() est bien injecté dans les prompts de
chaque section de document générée, y compris le résumé compressé (role=system).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.agents.base import AgentContext
from app.agents.execution_engine import (
    ExecutionEngine,
    _build_conversation_context,
)
from app.agents.triage import PlanDecision


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ctx(message: str = "Fais-moi un business plan", history: list[dict] | None = None) -> AgentContext:
    return AgentContext(
        user_id=uuid.uuid4(),
        message=message,
        history=history or [],
        goal_context={"document_type": "business_plan"},
    )


def _make_engine(llm) -> ExecutionEngine:
    get_agent = MagicMock(return_value=MagicMock())
    return ExecutionEngine(llm=llm, get_agent=get_agent)


# ── Tests unitaires : _build_conversation_context ───────────────────────────


class TestBuildConversationContext:
    def test_empty_history_returns_empty_string(self):
        assert _build_conversation_context([]) == ""

    def test_user_message_prefixed(self):
        history = [{"role": "user", "content": "Mon projet est une app mobile."}]
        result = _build_conversation_context(history)
        assert "Utilisateur : Mon projet est une app mobile." in result

    def test_assistant_message_prefixed(self):
        history = [{"role": "assistant", "content": "Je vais t'aider."}]
        result = _build_conversation_context(history)
        assert "Assistant : Je vais t'aider." in result

    def test_system_summary_prefixed(self):
        history = [{"role": "system", "content": "Résumé : utilisateur veut une app mobile."}]
        result = _build_conversation_context(history)
        assert "[Résumé de la conversation]" in result
        assert "Résumé : utilisateur veut une app mobile." in result

    def test_mixed_history_ordered(self):
        history = [
            {"role": "system", "content": "Résumé antérieur."},
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Réponse 1"},
        ]
        result = _build_conversation_context(history)
        assert "[Résumé de la conversation]" in result
        assert "Utilisateur : Question 1" in result
        assert "Assistant : Réponse 1" in result

    def test_empty_content_skipped(self):
        history = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "user", "content": "Vrai message."},
        ]
        result = _build_conversation_context(history)
        assert result.count("Utilisateur :") == 1
        assert "Vrai message." in result

    def test_content_truncated_at_4000(self):
        history = [{"role": "user", "content": "x" * 10_000}]
        result = _build_conversation_context(history)
        assert len(result) <= 4000

    def test_user_content_truncated_at_500(self):
        history = [{"role": "user", "content": "a" * 1000}]
        result = _build_conversation_context(history)
        # "Utilisateur : " + 500 chars (a) → le contenu est bien tronqué
        assert "a" * 501 not in result

    def test_bot_role_treated_as_assistant(self):
        history = [{"role": "bot", "content": "Bonne réponse."}]
        result = _build_conversation_context(history)
        assert "Assistant : Bonne réponse." in result

    def test_unknown_role_skipped(self):
        history = [
            {"role": "tool", "content": "Debug info."},
            {"role": "user", "content": "Vrai message."},
        ]
        result = _build_conversation_context(history)
        assert "Debug info." not in result
        assert "Vrai message." in result


# ── Tests d'intégration moteur : conversation_context injecté dans prompts ──


class TestExecutionEngineContextInjection:
    """Vérifie que _execute_document passe bien conversation_context au LLM."""

    @pytest.mark.asyncio
    async def test_conversation_context_injected_in_section_prompts(self):
        """Chaque section reçoit le contexte conversationnel dans son prompt utilisateur."""
        history = [
            {"role": "user", "content": "Mon budget est de 50 000 €."},
            {"role": "assistant", "content": "Parfait, je prends note."},
        ]
        ctx = _ctx(history=history)

        captured_prompts: list[str] = []

        async def fake_complete(messages, **kwargs):
            user_parts = [m["content"] for m in messages if m.get("role") == "user"]
            captured_prompts.extend(user_parts)
            return "Contenu de section."

        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=fake_complete)
        engine = _make_engine(llm)

        plan = PlanDecision(mode="direct", agent_type="document")

        with patch("app.agents.document_agent._is_follow_up", new=AsyncMock(return_value=False)):
            events = []
            async for event in engine.execute(plan, ctx):
                events.append(event)

        assert len(captured_prompts) > 0, "Le LLM doit avoir été appelé au moins une fois"
        context_present = any(
            "Mon budget est de 50 000 €." in p or "50 000" in p
            for p in captured_prompts
        )
        assert context_present, (
            "Le contexte de conversation (historique utilisateur) doit apparaître "
            "dans au moins un prompt de section.\n"
            f"Prompts capturés : {captured_prompts[:2]}"
        )

    @pytest.mark.asyncio
    async def test_system_summary_injected_in_section_prompts(self):
        """Le résumé compressé (role=system) est injecté dans les prompts de section."""
        history = [
            {"role": "system", "content": "L'utilisateur veut créer une startup edtech."},
        ]
        ctx = _ctx(history=history)

        captured_prompts: list[str] = []

        async def fake_complete(messages, **kwargs):
            user_parts = [m["content"] for m in messages if m.get("role") == "user"]
            captured_prompts.extend(user_parts)
            return "Section générée."

        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=fake_complete)
        engine = _make_engine(llm)

        plan = PlanDecision(mode="direct", agent_type="document")

        with patch("app.agents.document_agent._is_follow_up", new=AsyncMock(return_value=False)):
            async for _ in engine.execute(plan, ctx):
                pass

        summary_present = any("edtech" in p for p in captured_prompts)
        assert summary_present, (
            "Le résumé système doit être inclus dans les prompts de section.\n"
            f"Prompts capturés : {captured_prompts[:2]}"
        )

    @pytest.mark.asyncio
    async def test_no_history_no_conversation_context_block(self):
        """Sans historique, le bloc 'Contexte de la conversation' est absent des prompts."""
        ctx = _ctx(history=[])

        captured_prompts: list[str] = []

        async def fake_complete(messages, **kwargs):
            user_parts = [m["content"] for m in messages if m.get("role") == "user"]
            captured_prompts.extend(user_parts)
            return "Section."

        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=fake_complete)
        engine = _make_engine(llm)

        plan = PlanDecision(mode="direct", agent_type="document")

        with patch("app.agents.document_agent._is_follow_up", new=AsyncMock(return_value=False)):
            async for _ in engine.execute(plan, ctx):
                pass

        conversation_block_present = any(
            "Contexte de la conversation" in p
            for p in captured_prompts
        )
        assert not conversation_block_present, (
            "Sans historique, le bloc 'Contexte de la conversation' ne doit pas apparaître."
        )

    @pytest.mark.asyncio
    async def test_context_appears_in_all_sections(self):
        """Le même contexte conversationnel est injecté dans TOUTES les sections."""
        MARKER = "Chiffre clé : 42 millions."
        history = [{"role": "user", "content": MARKER}]
        ctx = _ctx(history=history)

        section_prompts: list[str] = []

        async def fake_complete(messages, **kwargs):
            user_parts = [m["content"] for m in messages if m.get("role") == "user"]
            # Exclure les mini-appels d'extraction de faits (< 200 chars)
            for p in user_parts:
                if "INSTRUCTION POUR CETTE SECTION" in p:
                    section_prompts.append(p)
            return "Section."

        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=fake_complete)
        engine = _make_engine(llm)

        plan = PlanDecision(mode="direct", agent_type="document")

        with patch("app.agents.document_agent._is_follow_up", new=AsyncMock(return_value=False)):
            async for _ in engine.execute(plan, ctx):
                pass

        assert len(section_prompts) >= 1, "Au moins une section doit avoir été générée"
        for i, prompt in enumerate(section_prompts):
            assert MARKER in prompt, (
                f"Section {i + 1} : le marqueur de contexte est absent du prompt.\n"
                f"Prompt : {prompt[:300]}"
            )
