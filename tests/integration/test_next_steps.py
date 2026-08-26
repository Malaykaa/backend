"""Exercice vs evaluation, et le parcours de la reussite.

Deux manques symetriques :

- le declenchement du plan ne distinguait pas un ENTRAINEMENT (tentatives
  illimitees, ou rater la premiere fois est le principe meme) d'une EVALUATION
  (notee, tentative unique) ;
- un eleve qui REUSSIT ne recevait rien : le parcours s'arretait sur la note,
  au moment ou il est pourtant le plus disponible pour la suite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.models.structure import (
    Classroom,
    ClassroomExercise,
    ClassroomExerciseKind,
    ClassroomExerciseQuestion,
    ClassroomMembership,
    MembershipStatus,
    Structure,
    StructureStatus,
)
from app.services import classroom_course_service, classroom_exercise_service


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


def _exercice(db_session, classroom, *, topic, kind, subject="Maths", n=4):
    ex = ClassroomExercise(
        id=uuid.uuid4(), classroom_id=classroom.id, title=f"QCM {topic}",
        subject=subject, kind=kind,
    )
    db_session.add(ex)
    db_session.flush()
    for i in range(n):
        db_session.add(ClassroomExerciseQuestion(
            id=uuid.uuid4(), exercise_id=ex.id, prompt=f"Q{i}",
            choices=["A", "B", "C"], correct_choice_index=0, points=1, order=i,
            topic_tag=topic,
        ))
    db_session.commit()
    return ex


def _tenter(db_session, exercise, user_id, *, juste):
    classroom_exercise_service.start_submission(
        db_session, exercise_id=exercise.id, user_id=user_id,
    )
    db_session.commit()
    sub = classroom_exercise_service.submit_exercise(
        db_session, exercise_id=exercise.id, user_id=user_id,
        answers=[(q.id, q.correct_choice_index if juste else 1) for q in exercise.questions],
    )
    db_session.commit()
    return sub


def _envoyer(db_session, exercise, user_id):
    classroom_exercise_service.send_exercise(
        db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
    )
    db_session.commit()


class TestExerciceVersusEvaluation:
    """Rater une premiere fois un ENTRAINEMENT est le principe meme de
    l'entrainement : y declencher une remediation serait premature."""

    def test_evaluation_ratee_declenche_des_la_premiere_fois(self):
        assert classroom_course_service.needs_support_plan(
            30, kind=ClassroomExerciseKind.evaluation, failed_attempts=1,
        ) is True

    def test_exercice_rate_une_fois_ne_declenche_pas(self):
        assert classroom_course_service.needs_support_plan(
            30, kind=ClassroomExerciseKind.exercise, failed_attempts=1,
        ) is False

    def test_exercice_rate_deux_fois_declenche(self):
        """Un echec repete est le signe d'un eleve bloque, non d'un eleve en
        train d'apprendre."""
        assert classroom_course_service.needs_support_plan(
            30, kind=ClassroomExerciseKind.exercise, failed_attempts=2,
        ) is True

    def test_reussite_ne_declenche_jamais(self):
        for kind in (ClassroomExerciseKind.exercise, ClassroomExerciseKind.evaluation):
            assert classroom_course_service.needs_support_plan(
                80, kind=kind, failed_attempts=5,
            ) is False


class TestParcoursDeLaReussite:
    def _opportunite(self, db_session, titre, offer_type=ScrapedOfferType.job):
        db_session.add(ScrapedOffer(
            id=uuid.uuid4(), source="test", offer_type=offer_type,
            external_id=uuid.uuid4().hex, title=titre,
            description="Poste ouvert.", is_active=True,
            scraped_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

    def test_une_notion_maitrisee_ouvre_des_opportunites_reelles(
        self, db_session, classroom, make_user,
    ):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        notion = f"topnxt{uuid.uuid4().hex[:8]}"
        titre = f"Stage {notion} junior"
        self._opportunite(db_session, titre)

        ex = _exercice(db_session, classroom, topic=notion, kind=ClassroomExerciseKind.evaluation)
        _envoyer(db_session, ex, user_id)
        _tenter(db_session, ex, user_id, juste=True)

        items = classroom_exercise_service.get_my_next_steps(db_session, user_id=user_id)

        assert len(items) == 1
        assert [m["topic_tag"] for m in items[0]["mastered_topics"]] == [notion]
        assert titre in [o["title"] for o in items[0]["opportunities"]]

    def test_un_eleve_en_echec_n_a_pas_de_notion_maitrisee(
        self, db_session, classroom, make_user,
    ):
        """On n'annonce pas un acquis qui n'a pas ete demontre."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        notion = f"topnxt{uuid.uuid4().hex[:8]}"

        ex = _exercice(db_session, classroom, topic=notion, kind=ClassroomExerciseKind.evaluation)
        _envoyer(db_session, ex, user_id)
        _tenter(db_session, ex, user_id, juste=False)

        assert classroom_exercise_service.get_my_next_steps(db_session, user_id=user_id) == []

    def test_difficulte_et_reussite_restent_coherentes(
        self, db_session, classroom, make_user,
    ):
        """Une meme notion ne peut pas etre a la fois ratee et maitrisee."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        rate = f"topnxt{uuid.uuid4().hex[:8]}"
        acquis = f"topnxt{uuid.uuid4().hex[:8]}"

        ex_rate = _exercice(db_session, classroom, topic=rate, kind=ClassroomExerciseKind.evaluation)
        _envoyer(db_session, ex_rate, user_id)
        _tenter(db_session, ex_rate, user_id, juste=False)

        ex_ok = _exercice(db_session, classroom, topic=acquis, kind=ClassroomExerciseKind.evaluation)
        _envoyer(db_session, ex_ok, user_id)
        _tenter(db_session, ex_ok, user_id, juste=True)

        difficultes = classroom_exercise_service.get_my_difficulty(db_session, user_id=user_id)
        suites = classroom_exercise_service.get_my_next_steps(db_session, user_id=user_id)

        rates = {f["topic_tag"] for it in difficultes for f in it["flagged_topics"]}
        maitrises = {m["topic_tag"] for it in suites for m in it["mastered_topics"]}

        assert rate in rates and acquis not in rates
        assert acquis in maitrises and rate not in maitrises
        assert rates.isdisjoint(maitrises)
