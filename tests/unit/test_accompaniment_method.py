"""Verrouille le passage de la 'méthode d'accompagnement' (compétences actuelles
→ contexte → écart/formation → offres) de l'agent d'orientation seul à TOUS les
agents spécialisés d'accompagnement (stage, concours, bourse, freelance, suivi
scolaire...), sans y ajouter le double appel LLM d'un chaînage d'agents :

1. `_ACCOMPANIMENT_METHOD` est injecté dans `_build_messages()` pour tout agent
   sauf document/free (génération formatée, chat hors-scope) et
   teacher_course/evolution_plan (Malayka Institution — génération en une passe).
2. `ChatService._enrich_with_careers`/`_enrich_with_careers_async` ne réservent
   plus les fiches métiers au seul goal_type orientation — même liste
   d'exclusion que les offres (document/free).
3. `_INTENT_OFFER_TYPES` couvre désormais orientation/freelance/coursework, qui
   retournaient silencieusement [] auparavant (clé absente du dict).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.agents.base import AgentContext, SpecializedAgent
from app.repositories.scraped_offer_repo import _INTENT_OFFER_TYPES
from app.services.chat_service import ChatService, _enrich_with_careers_async


class _DummyAgent(SpecializedAgent):
    AGENT_ID = "career"
    SYSTEM_PROMPT = "Tu es un agent de test."


def _ctx() -> AgentContext:
    return AgentContext(user_id=uuid.uuid4(), message="je cherche un stage")


class TestAccompanimentMethodInjection:
    def test_injecte_pour_un_agent_specialise_standard(self):
        agent = _DummyAgent(llm=None)
        messages = agent._build_messages(_ctx())
        assert any("Méthode d'accompagnement" in m["content"] for m in messages)

    @pytest.mark.parametrize("agent_id", ["document", "free", "teacher_course", "evolution_plan"])
    def test_exclu_pour_les_agents_hors_accompagnement(self, agent_id):
        class _Agent(SpecializedAgent):
            AGENT_ID = agent_id
            SYSTEM_PROMPT = "x"

        messages = _Agent(llm=None)._build_messages(_ctx())
        assert not any("Méthode d'accompagnement" in m["content"] for m in messages)


class TestCareerEnrichmentGateElargie:
    @pytest.mark.parametrize(
        "goal_type", ["career", "exam", "freelance", "funding", "scholarship",
                      "study_grant", "tender", "coursework", "orientation"],
    )
    def test_service_appele_pour_tout_goal_type_d_accompagnement(self, goal_type):
        db = MagicMock()
        with patch("app.services.chat_service.CareerReferenceService") as MockSvc:
            MockSvc.return_value.search_for_agent.return_value = []
            ChatService._enrich_with_careers({}, goal_type, {}, "msg", db)
        MockSvc.return_value.search_for_agent.assert_called_once()

    @pytest.mark.parametrize("goal_type", ["document", "free", None])
    def test_service_non_appele_pour_document_free_ou_absent(self, goal_type):
        db = MagicMock()
        with patch("app.services.chat_service.CareerReferenceService") as MockSvc:
            ChatService._enrich_with_careers({}, goal_type, {}, "msg", db)
        MockSvc.return_value.search_for_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_variante_async_meme_porte(self):
        with patch("app.services.chat_service.CareerReferenceService") as MockSvc:
            MockSvc.return_value.search_for_agent.return_value = []
            await _enrich_with_careers_async({}, "freelance", {}, "msg")
        MockSvc.return_value.search_for_agent.assert_called_once()

        with patch("app.services.chat_service.CareerReferenceService") as MockSvc:
            await _enrich_with_careers_async({}, "document", {}, "msg")
        MockSvc.return_value.search_for_agent.assert_not_called()


class TestIntentOfferTypesCouvertureComplete:
    @pytest.mark.parametrize("intent", ["orientation", "freelance", "coursework"])
    def test_intents_precedemment_manquants_ont_maintenant_des_types(self, intent):
        assert _INTENT_OFFER_TYPES.get(intent), f"{intent} devrait avoir des types d'offres associés"
