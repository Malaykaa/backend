"""Robustesse de la génération de plans d'évolution en lot.

Cette fonction enchaîne un appel LLM complet par étudiant ET par matière,
séquentiellement, dans une seule requête HTTP. Trois défauts en découlaient :

- un double clic relançait toute la génération, et donc toute la facture ;
- un échec appelait db.rollback(), ce qui annulait aussi les plans déjà
  produits dans le lot (seulement flush()és, le commit n'ayant lieu qu'à la fin
  côté routeur) — tout en continuant la boucle et en annonçant un compteur faux ;
- un dépassement du délai HTTP (nginx coupe à 120 s) perdait tout le travail.

L'agent est mocké : ces tests verrouillent l'orchestration du lot, pas la
génération elle-même.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base import AgentResponse, Step
from app.models.structure import (
    Classroom,
    ClassroomCourse,
    ClassroomCourseKind,
    ClassroomCourseRecipient,
    ClassroomMembership,
    MembershipStatus,
    Structure,
    StructureStatus,
)
from app.services import classroom_course_service


@pytest.fixture()
def classroom(db_session):
    structure = Structure(id=uuid.uuid4(), name="Lycée Test", status=StructureStatus.active)
    db_session.add(structure)
    db_session.flush()
    room = Classroom(
        id=uuid.uuid4(), structure_id=structure.id, name="Terminale S",
        invite_code=uuid.uuid4().hex[:12],
    )
    db_session.add(room)
    db_session.commit()
    return room


def _send_course(db_session, classroom, user_id, subject="Maths"):
    """Crée un cours et le matérialise pour cet étudiant, avec une étape."""
    course = ClassroomCourse(
        id=uuid.uuid4(), classroom_id=classroom.id, title=f"Cours {subject}",
        subject=subject, kind=ClassroomCourseKind.course,
        summary="s", explanation="e",
    )
    db_session.add(course)
    db_session.flush()
    from app.models.structure import ClassroomCourseStep
    db_session.add(ClassroomCourseStep(
        id=uuid.uuid4(), course_id=course.id, label="Étape 1", description="d", order=1,
    ))
    db_session.flush()
    classroom_course_service._materialize_recipient(db_session, course, user_id)
    db_session.commit()
    return course


def _enroll(db_session, classroom, user_id):
    db_session.add(ClassroomMembership(
        id=uuid.uuid4(), classroom_id=classroom.id, user_id=user_id,
        requested_first_name="Test", requested_last_name="Student",
        status=MembershipStatus.accepted,
    ))
    db_session.commit()


def _fake_agent(response=None):
    """Remplace EvolutionPlanAgent par un agent qui répond instantanément."""
    resp = response or AgentResponse(
        explanation="Plan personnalisé.", agent_id="evolution_plan",
        steps=[Step(id="1", label="Consolider", description="d", order=1)],
    )
    agent = AsyncMock()
    agent.process = AsyncMock(return_value=resp)
    return agent


def _run(db_session, classroom, created_by, agent):
    with patch.object(classroom_course_service, "EvolutionPlanAgent", return_value=agent), \
         patch.object(classroom_course_service, "get_llm_provider", return_value=object()):
        return asyncio.run(classroom_course_service.generate_evolution_plans(
            db_session, classroom_id=classroom.id, created_by_user_id=created_by,
        ))


def _plans_en_base(db_session, classroom):
    return db_session.query(ClassroomCourse).filter(
        ClassroomCourse.classroom_id == classroom.id,
        ClassroomCourse.kind == ClassroomCourseKind.evolution_plan,
    ).all()


class TestReprisEtIdempotence:
    def test_relancer_ne_regenere_pas_les_plans_existants(self, db_session, classroom, make_user):
        """Le point qui rend le dépassement de délai inoffensif : relancer reprend
        là où la génération s'est arrêtée au lieu de tout refaire — et donc de
        tout refacturer."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _send_course(db_session, classroom, user_id)
        teacher_id, _ = make_user()

        agent = _fake_agent()
        premiers = _run(db_session, classroom, teacher_id, agent)
        assert len(premiers) == 1
        assert agent.process.await_count == 1

        seconds = _run(db_session, classroom, teacher_id, agent)
        assert seconds == [], "un couple déjà pourvu ne doit pas être regénéré"
        assert agent.process.await_count == 1, "aucun appel LLM supplémentaire"
        assert len(_plans_en_base(db_session, classroom)) == 1


class TestEchecPartiel:
    def test_un_echec_ne_detruit_pas_les_plans_deja_produits(self, db_session, classroom, make_user):
        """Avant correctif : db.rollback() annulait tout le lot, alors que la
        boucle continuait et que le compteur annonçait des plans disparus."""
        u1, _ = make_user()
        u2, _ = make_user()
        for uid in (u1, u2):
            _enroll(db_session, classroom, uid)
            _send_course(db_session, classroom, uid)
        teacher_id, _ = make_user()

        agent = AsyncMock()
        ok = AgentResponse(
            explanation="Plan ok.", agent_id="evolution_plan",
            steps=[Step(id="1", label="Consolider", description="d", order=1)],
        )
        # Le premier étudiant réussit, le second échoue.
        agent.process = AsyncMock(side_effect=[ok, RuntimeError("LLM indisponible")])

        generated = _run(db_session, classroom, teacher_id, agent)
        assert len(generated) == 1

        # Requêter la même session ne prouverait rien : une écriture seulement
        # flush()ée y reste visible. On annule la transaction courante — seul ce
        # qui a été réellement VALIDÉ survit.
        db_session.rollback()
        assert len(_plans_en_base(db_session, classroom)) == 1, (
            "le plan réussi doit être committé, pas seulement flushé"
        )


class TestVerrouDeSalle:
    def test_le_verrou_est_relache_apres_generation(self, db_session, classroom, make_user):
        """Un verrou consultatif de SESSION n'est pas relâché au retour de la
        connexion au pool. Sans libération explicite, la première génération
        bloquerait définitivement toutes les suivantes sur cette salle."""
        u1, _ = make_user()
        _enroll(db_session, classroom, u1)
        _send_course(db_session, classroom, u1, subject="Maths")
        teacher_id, _ = make_user()

        _run(db_session, classroom, teacher_id, _fake_agent())

        # Une nouvelle matière → il reste du travail. Si le verrou était encore
        # tenu, l'appel renverrait [] sans rien tenter.
        u2, _ = make_user()
        _enroll(db_session, classroom, u2)
        _send_course(db_session, classroom, u2, subject="Physique")

        agent = _fake_agent()
        generated = _run(db_session, classroom, teacher_id, agent)

        assert agent.process.await_count > 0, "le verrou n'a pas été relâché"
        assert len(generated) > 0
