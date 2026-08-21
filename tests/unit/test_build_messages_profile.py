"""_build_messages formate le profil transmis au LLM — verrouille deux points
trouvés en vérifiant le câblage interests/self_description (cf.
test_extract_profile.py pour l'autre bout de la chaîne) :
- une valeur liste (ex. interests) doit rester lisible ("a, b"), jamais le
  repr Python brut ("['a', 'b']") ;
- self_description (paragraphe libre) doit avoir son propre message, pas être
  noyé dans la ligne compacte "clé: valeur, clé: valeur".
"""

import uuid

from app.agents.base import AgentContext, SpecializedAgent


class _DummyAgent(SpecializedAgent):
    AGENT_ID = "test"
    SYSTEM_PROMPT = "Tu es un agent de test."


def _ctx_with_profile(profile: dict) -> AgentContext:
    return AgentContext(user_id=uuid.uuid4(), message="bonjour", profile=profile)


class TestFormatageProfil:
    def test_liste_rendue_lisible(self):
        agent = _DummyAgent(llm=None)
        messages = agent._build_messages(_ctx_with_profile({"interests": ["informatique", "design"]}))
        profile_msg = next(m for m in messages if m["content"].startswith("Profil de l'utilisateur"))
        assert "informatique, design" in profile_msg["content"]
        assert "[" not in profile_msg["content"]

    def test_self_description_a_son_propre_message(self):
        agent = _DummyAgent(llm=None)
        messages = agent._build_messages(
            _ctx_with_profile({"domain": "Informatique", "self_description": "Je cherche un stage."})
        )
        profile_msg = next(m for m in messages if m["content"].startswith("Profil de l'utilisateur"))
        assert "self_description" not in profile_msg["content"]
        desc_msg = next(m for m in messages if "Je cherche un stage." in m["content"])
        assert "donnée de lui-même" in desc_msg["content"]

    def test_pas_de_self_description_ne_cree_pas_de_message_vide(self):
        agent = _DummyAgent(llm=None)
        messages = agent._build_messages(_ctx_with_profile({"domain": "Informatique"}))
        assert not any("donnée de lui-même" in m["content"] for m in messages)
