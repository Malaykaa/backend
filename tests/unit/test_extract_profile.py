"""extract_profile() est le point de passage unique qui construit le dict
profil transmis à TOUS les agents (chat.py, action_adapter.py, documents.py,
plans.py, trends.py — cf. grep). Un champ Profile qui n'y figure pas n'atteint
jamais aucun agent, même si `career_reference_service` ou tout autre service
sait le lire — ces tests verrouillent que interests/self_description y sont
bien inclus (bug réel trouvé : ils avaient été ajoutés au modèle et au
formulaire, jamais à cette liste codée en dur).
"""

from types import SimpleNamespace

from app.core.deps import extract_profile


def _user_with_profile(**overrides):
    defaults = dict(
        first_name="Awa", last_name="Koné", gender="F", birth_year=2000,
        country="CI", primary_role="student", domain=None, field_of_study="Info",
        current_status=None, preferred_content=None, skills=[],
        interests=["informatique", "design"], self_description="Je cherche un stage.",
    )
    defaults.update(overrides)
    profile = SimpleNamespace(**defaults)
    return SimpleNamespace(profile=profile)


class TestExtractProfile:
    def test_interests_est_inclus(self):
        result = extract_profile(_user_with_profile())
        assert result["interests"] == ["informatique", "design"]

    def test_self_description_est_inclus(self):
        result = extract_profile(_user_with_profile())
        assert result["self_description"] == "Je cherche un stage."

    def test_profil_absent_retourne_dict_vide(self):
        user = SimpleNamespace(profile=None)
        assert extract_profile(user) == {}
