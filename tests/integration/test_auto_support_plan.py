"""Un resultat insuffisant declenche un plan d'accompagnement, et des ressources.

Les objectifs du cours SONT les evaluations : rater la moitie des points, c'est
ne pas avoir atteint l'objectif. Le plan part donc automatiquement, sans
attendre une action de l'enseignant.

Les ressources proposees viennent de scraped_offers (types formation et
resource) : ce sont des lignes reelles de la base, jamais du contenu genere.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base import AgentResponse, Step
from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.models.structure import (
    Classroom,
    ClassroomCourse,
    ClassroomCourseKind,
    ClassroomCourseStep,
    ClassroomExercise,
    ClassroomExerciseKind,
    ClassroomExerciseQuestion,
    ClassroomMembership,
    MembershipStatus,
    Structure,
    StructureStatus,
)
from app.services import (
    classroom_course_service,
    classroom_exercise_service,
    scraped_offer_service,
)


@pytest.fixture()
def classroom(db_session):
    st = Structure(id=uuid.uuid4(), name="Lycee Test", status=StructureStatus.active)
    db_session.add(st)
    db_session.flush()
    room = Classroom(
        id=uuid.uuid4(), structure_id=st.id, name="Terminale S",
        invite_code=uuid.uuid4().hex[:12],
    )
    db_session.add(room)
    db_session.commit()
    return room


def _enroll(db_session, classroom, user_id):
    db_session.add(ClassroomMembership(
        id=uuid.uuid4(), classroom_id=classroom.id, user_id=user_id,
        requested_first_name="Test", requested_last_name="Student",
        status=MembershipStatus.accepted,
    ))
    db_session.commit()


def _cours(db_session, classroom, user_id, subject="Maths"):
    course = ClassroomCourse(
        id=uuid.uuid4(), classroom_id=classroom.id, title=f"Cours {subject}",
        subject=subject, kind=ClassroomCourseKind.course, summary="s", explanation="e",
    )
    db_session.add(course)
    db_session.flush()
    db_session.add(ClassroomCourseStep(
        id=uuid.uuid4(), course_id=course.id, label="Etape 1", description="d", order=1,
    ))
    db_session.flush()
    classroom_course_service._materialize_recipient(db_session, course, user_id)
    db_session.commit()
    return course


def _passer_exercice(db_session, classroom, user_id, *, topic, subject="Maths", juste=False, n=4):
    exercise = ClassroomExercise(
        id=uuid.uuid4(), classroom_id=classroom.id, title=f"QCM {topic}",
        subject=subject, kind=ClassroomExerciseKind.evaluation,
    )
    db_session.add(exercise)
    db_session.flush()
    for i in range(n):
        db_session.add(ClassroomExerciseQuestion(
            id=uuid.uuid4(), exercise_id=exercise.id, prompt=f"Q{i}",
            choices=["A", "B", "C"], correct_choice_index=0, points=1, order=i,
            topic_tag=topic,
        ))
    db_session.commit()
    classroom_exercise_service.send_exercise(
        db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
    )
    db_session.commit()
    classroom_exercise_service.start_submission(
        db_session, exercise_id=exercise.id, user_id=user_id,
    )
    db_session.commit()
    submission = classroom_exercise_service.submit_exercise(
        db_session, exercise_id=exercise.id, user_id=user_id,
        answers=[(q.id, q.correct_choice_index if juste else 1) for q in exercise.questions],
    )
    db_session.commit()
    return exercise, submission


def _fake_agent():
    agent = AsyncMock()
    agent.process = AsyncMock(return_value=AgentResponse(
        explanation="Voici comment reprendre cette notion.", agent_id="evolution_plan",
        steps=[Step(id="1", label="Revoir la notion", description="d", order=1)],
    ))
    return agent


def _generer(db_session, classroom, user_id, agent, subject="Maths"):
    with patch.object(classroom_course_service, "EvolutionPlanAgent", return_value=agent), \
         patch.object(classroom_course_service, "get_llm_provider", return_value=object()):
        return asyncio.run(classroom_course_service.generate_support_plan_for_student(
            db_session, classroom_id=classroom.id, user_id=user_id, subject=subject,
        ))


def _plans(db_session, classroom):
    return db_session.query(ClassroomCourse).filter(
        ClassroomCourse.classroom_id == classroom.id,
        ClassroomCourse.kind == ClassroomCourseKind.evolution_plan,
    ).all()


class TestRegleDeDeclenchement:
    """Les objectifs du cours SONT les evaluations : sous la moitie des points,
    l'objectif n'est pas atteint."""

    @pytest.mark.parametrize(
        "score, attendu", [(0, True), (49, True), (50, False), (75, False), (None, False)],
    )
    def test_seuil(self, score, attendu):
        assert classroom_course_service.needs_support_plan(score) is attendu


class TestPlanAutomatique:
    def test_evaluation_ratee_declenche_un_plan(self, db_session, classroom, make_user):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _cours(db_session, classroom, user_id)
        _, submission = _passer_exercice(db_session, classroom, user_id, topic="derivees")

        assert classroom_course_service.needs_support_plan(submission.score_pct) is True

        plan = _generer(db_session, classroom, user_id, _fake_agent())
        assert plan is not None
        assert plan.kind == ClassroomCourseKind.evolution_plan
        # created_by_user_id NULL distingue un plan automatique d'un plan
        # declenche par un enseignant, sans champ supplementaire.
        assert plan.created_by_user_id is None

    def test_le_plan_s_appuie_sur_la_notion_ratee(self, db_session, classroom, make_user):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _cours(db_session, classroom, user_id)
        _passer_exercice(db_session, classroom, user_id, topic="derivees composees")

        agent = _fake_agent()
        _generer(db_session, classroom, user_id, agent)

        message = agent.process.await_args.args[0].message
        assert "derivees composees" in message
        assert "Résultats aux exercices" in message

    def test_cooldown_evite_le_spam_et_la_surfacturation(self, db_session, classroom, make_user):
        """Un eleve qui enchaine plusieurs exercices rates ne doit pas recevoir un
        plan a chaque soumission."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _cours(db_session, classroom, user_id)
        _passer_exercice(db_session, classroom, user_id, topic="derivees")

        agent = _fake_agent()
        assert _generer(db_session, classroom, user_id, agent) is not None
        assert agent.process.await_count == 1

        assert _generer(db_session, classroom, user_id, agent) is None
        assert agent.process.await_count == 1, "aucun appel LLM supplementaire"
        assert len(_plans(db_session, classroom)) == 1

    def test_apres_le_cooldown_un_nouveau_plan_est_possible(
        self, db_session, classroom, make_user,
    ):
        """Contre-epreuve : le cooldown limite le rythme, il ne bloque pas
        definitivement un eleve qui rate a nouveau des semaines plus tard."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _cours(db_session, classroom, user_id)
        _passer_exercice(db_session, classroom, user_id, topic="derivees")

        plan = _generer(db_session, classroom, user_id, _fake_agent())
        plan.created_at = datetime.now(timezone.utc) - timedelta(
            days=classroom_course_service.SUPPORT_PLAN_COOLDOWN_DAYS + 1,
        )
        db_session.commit()

        assert _generer(db_session, classroom, user_id, _fake_agent()) is not None
        assert len(_plans(db_session, classroom)) == 2

    def test_sans_cours_recu_aucun_plan(self, db_session, classroom, make_user):
        """Sans progression a resumer, l'agent n'aurait rien sur quoi s'appuyer."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _passer_exercice(db_session, classroom, user_id, topic="derivees")

        agent = _fake_agent()
        assert _generer(db_session, classroom, user_id, agent) is None
        assert agent.process.await_count == 0


class TestRessourcesComplementaires:
    """La fixture db_session ne nettoie pas entre les tests (propriete existante
    de la suite) : chaque test utilise donc une notion UNIQUE, pour qu'une
    ressource laissee par un autre test ne puisse pas la faire correspondre."""

    def _ressource(self, db_session, titre, offer_type=ScrapedOfferType.formation):
        db_session.add(ScrapedOffer(
            id=uuid.uuid4(), source="test", offer_type=offer_type,
            external_id=uuid.uuid4().hex, title=titre,
            description="Contenu de remise a niveau.", is_active=True,
            scraped_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

    def test_les_notions_ratees_remontent_des_ressources_reelles(
        self, db_session, classroom, make_user,
    ):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        notion = f"topokx{uuid.uuid4().hex[:8]}"
        titre = f"Formation {notion} niveau 1"
        self._ressource(db_session, titre)

        _passer_exercice(db_session, classroom, user_id, topic=notion)

        items = classroom_exercise_service.get_my_difficulty(db_session, user_id=user_id)
        titres = [r["title"] for it in items for r in it["resources"]]
        assert titre in titres

    def test_aucune_ressource_pertinente_ne_renvoie_rien(
        self, db_session, classroom, make_user,
    ):
        """Mieux vaut ne rien proposer qu'une ressource hors sujet — et surtout
        jamais en inventer une."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        self._ressource(db_session, "Formation en boulangerie artisanale")

        # Notion unique : rien en base ne peut y correspondre.
        _passer_exercice(db_session, classroom, user_id, topic=f"topokx{uuid.uuid4().hex[:8]}")

        items = classroom_exercise_service.get_my_difficulty(db_session, user_id=user_id)
        assert all(it["resources"] == [] for it in items)

    def test_seuls_les_types_pedagogiques_sont_proposes(self, db_session):
        """Une offre d'emploi ne repond pas a « je bute sur cette notion »."""
        notion = f"topokx{uuid.uuid4().hex[:8]}"
        db_session.add(ScrapedOffer(
            id=uuid.uuid4(), source="test", offer_type=ScrapedOfferType.job,
            external_id=uuid.uuid4().hex, title=f"Emploi professeur de {notion}",
            is_active=True, scraped_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        trouve = scraped_offer_service.search_resources_for_topics(db_session, [notion])
        assert trouve == [], "une offre d'emploi n'est pas une ressource pedagogique"
