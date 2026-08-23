"""Les « champs critiques » signalés manquants à l'agent doivent exister.

_get_missing_critical_fields injecte un message système disant à l'agent quels
champs de profil restent inconnus, pour qu'il les priorise dans son diagnostic.
Il lit ces champs dans le dict produit par core.deps.extract_profile : une clé
absente de ce dict est donc signalée manquante à CHAQUE message, même quand
l'information est renseignée en base.

C'était le cas de "level" pour les objectifs bourse et études à l'étranger — le
niveau d'études saisi à l'inscription est rangé dans current_status
(cf. OnboardingPage.tsx : current_status = studyLevel || currentStatus).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.base import _CRITICAL_FIELDS, _get_missing_critical_fields
from app.core.deps import extract_profile


def _full_profile_keys() -> set[str]:
    """Clés réellement transmises aux agents."""
    profile = SimpleNamespace(
        first_name="A", last_name="B", gender="F", birth_year=2000, country="CI",
        city="Bouaké", nationality="Ivoirienne", primary_role="student",
        domain="Informatique", field_of_study="Info", current_status="Licence 3",
        preferred_content=None, skills=[], interests=[], self_description="",
    )
    return set(extract_profile(SimpleNamespace(profile=profile)))


class TestCoherenceDesChampsCritiques:
    def test_tout_champ_critique_existe_dans_le_profil_transmis(self):
        """Verrou anti-régression : ajouter un champ critique fantôme ferait
        reposer indéfiniment une question déjà résolue."""
        connus = _full_profile_keys()
        fantomes = {
            (goal, key)
            for goal, fields in _CRITICAL_FIELDS.items()
            for key, _ in fields
            if key not in connus
        }
        assert not fantomes, f"champs critiques absents du profil transmis : {fantomes}"

    def test_niveau_detudes_renseigne_nest_plus_signale_manquant(self):
        profile = {"country": "CI", "domain": "Informatique", "current_status": "Licence 3"}
        manquants = _get_missing_critical_fields(profile, "scholarship")
        assert manquants == []

    def test_niveau_detudes_absent_reste_signale(self):
        """Contre-épreuve : le signalement doit continuer de fonctionner."""
        profile = {"country": "CI", "domain": "Informatique"}
        cles = [key for key, _ in _get_missing_critical_fields(profile, "scholarship")]
        assert cles == ["current_status"]
