"""career_reference_service — recherche par pays/mots-clés et résolution de référence.

Le référentiel est volontairement petit (curaté à la main) : pas d'embeddings,
un simple filtre pays + ILIKE suffit. Ces tests verrouillent le comportement
de recherche et l'idempotence du script de seed.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.career_reference import CareerReference
from app.services.career_reference_service import CareerReferenceService


def _make_career(db_session, **overrides) -> CareerReference:
    defaults = dict(
        id=uuid.uuid4(), title="Développeur web", category="Informatique",
        description="Conçoit des applications web.", key_skills=["JavaScript"],
        example_formations=[{"label": "Licence Info", "level": "Bac+3", "city": "Abidjan"}],
        country="CI",
    )
    defaults.update(overrides)
    career = CareerReference(**defaults)
    db_session.add(career)
    db_session.commit()
    return career


class TestSearchForAgent:
    def test_filtre_par_pays(self, db_session):
        _make_career(db_session, title="Développeur web", country="CI")
        _make_career(db_session, title="Développeur web France", country="FR")

        results = CareerReferenceService(db_session).search_for_agent(
            profile={"country": "CI"}, message="développeur",
        )
        titles = [r["title"] for r in results]
        assert "Développeur web" in titles
        assert "Développeur web France" not in titles

    def test_pays_generique_null_toujours_inclus(self, db_session):
        _make_career(db_session, title="Métier générique", country=None)
        results = CareerReferenceService(db_session).search_for_agent(
            profile={"country": "CI"}, message="métier",
        )
        assert any(r["title"] == "Métier générique" for r in results)

    def test_les_interets_declares_alimentent_le_matching(self, db_session):
        """Les intérêts déclarés (nouveau champ Profile) doivent participer à la
        recherche de mots-clés, au même titre que domain/field_of_study."""
        _make_career(db_session, title="Technicien agricole", category="Agriculture")
        _make_career(db_session, title="Développeur web", category="Informatique")

        results = CareerReferenceService(db_session).search_for_agent(
            profile={"country": "CI", "interests": ["agriculture", "élevage"]},
            message="je ne sais pas quoi faire",
        )
        titles = [r["title"] for r in results]
        assert "Technicien agricole" in titles
        assert "Développeur web" not in titles

    def test_filtre_par_mots_cles(self, db_session):
        _make_career(db_session, title="Développeur web", category="Informatique")
        _make_career(db_session, title="Infirmier", category="Santé")

        results = CareerReferenceService(db_session).search_for_agent(
            profile={"country": "CI"}, message="je veux travailler en informatique",
        )
        titles = [r["title"] for r in results]
        assert "Développeur web" in titles
        assert "Infirmier" not in titles

    def test_aucun_mot_cle_ne_correspond_ne_retourne_rien(self, db_session):
        _make_career(db_session, title="Développeur web")
        results = CareerReferenceService(db_session).search_for_agent(
            profile={"country": "CI"}, message="zzzmotclefantaisistequinexistepasxyz",
        )
        assert results == []

    def test_serialisation_ne_contient_pas_les_champs_internes(self, db_session):
        career = _make_career(db_session, title="Boulanger-pâtissier spécialisé")
        results = CareerReferenceService(db_session).search_for_agent(
            profile={"country": "CI"}, message="boulanger",
        )
        assert results[0]["career_ref"] == f"career:{career.id}"
        assert "id" not in results[0]
        assert "reviewed_by" not in results[0]


class TestGetByRef:
    def test_reference_valide(self, db_session):
        career = _make_career(db_session)
        result = CareerReferenceService(db_session).get_by_ref(f"career:{career.id}")
        assert result is not None
        assert result["title"] == "Développeur web"

    def test_reference_malformee(self, db_session):
        assert CareerReferenceService(db_session).get_by_ref("pas-une-ref") is None

    def test_reference_inexistante(self, db_session):
        assert CareerReferenceService(db_session).get_by_ref(f"career:{uuid.uuid4()}") is None
