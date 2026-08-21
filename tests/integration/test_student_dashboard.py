"""Index élève (my-deliveries) — combine cours et exercices reçus, tous confondus.

Corrige l'absence de point d'entrée listant ce qu'un élève a reçu : jusqu'ici
seul le lien de la notification annonçant un envoi y donnait accès.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.structure import (
    Classroom,
    ClassroomCourse,
    ClassroomExercise,
    ClassroomExerciseKind,
    ClassroomExerciseQuestion,
    ClassroomMembership,
    MembershipStatus,
    Structure,
    StructureStatus,
)
from app.services import classroom_course_service, classroom_exercise_service, student_dashboard_service


@pytest.fixture()
def classroom(db_session):
    structure = Structure(id=uuid.uuid4(), name="Lycée Test", status=StructureStatus.active)
    db_session.add(structure)
    db_session.flush()
    classroom = Classroom(
        id=uuid.uuid4(), structure_id=structure.id, name="Terminale S",
        invite_code=uuid.uuid4().hex[:12],
    )
    db_session.add(classroom)
    db_session.commit()
    return classroom


@pytest.fixture()
def enroll(db_session, classroom):
    def _enroll(user_id: uuid.UUID) -> None:
        db_session.add(ClassroomMembership(
            id=uuid.uuid4(), classroom_id=classroom.id, user_id=user_id,
            requested_first_name="Test", requested_last_name="Student",
            status=MembershipStatus.accepted,
        ))
        db_session.commit()
    return _enroll


class TestListMyDeliveries:
    def test_eleve_sans_envoi_recoit_une_liste_vide(self, db_session, make_user):
        user_id, _ = make_user()
        items = student_dashboard_service.list_my_deliveries(db_session, user_id)
        assert items == []

    def test_combine_cours_et_exercice_pour_un_meme_eleve(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)

        course = ClassroomCourse(
            id=uuid.uuid4(), classroom_id=classroom.id, title="Cours dérivées",
            summary="résumé", explanation="explication",
        )
        db_session.add(course)
        db_session.flush()
        db_session.commit()
        classroom_course_service.send_course(db_session, course_id=course.id, target="student", student_user_id=user_id)
        db_session.commit()

        exercise = ClassroomExercise(
            id=uuid.uuid4(), classroom_id=classroom.id, title="QCM dérivées",
            kind=ClassroomExerciseKind.exercise, topic_hint="dérivées",
        )
        db_session.add(exercise)
        db_session.flush()
        db_session.add(ClassroomExerciseQuestion(
            id=uuid.uuid4(), exercise_id=exercise.id, prompt="Q1",
            choices=["A", "B"], correct_choice_index=0, order=0,
        ))
        db_session.commit()
        classroom_exercise_service.send_exercise(db_session, exercise_id=exercise.id, target="student", student_user_id=user_id)
        db_session.commit()

        items = student_dashboard_service.list_my_deliveries(db_session, user_id)
        kinds = {i["kind"] for i in items}
        assert kinds == {"course", "exercise"}
        assert len(items) == 2

    def test_score_dun_exercice_soumis_apparait(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)

        exercise = ClassroomExercise(
            id=uuid.uuid4(), classroom_id=classroom.id, title="QCM dérivées",
            kind=ClassroomExerciseKind.exercise, topic_hint="dérivées",
        )
        db_session.add(exercise)
        db_session.flush()
        db_session.add(ClassroomExerciseQuestion(
            id=uuid.uuid4(), exercise_id=exercise.id, prompt="Q1",
            choices=["A", "B"], correct_choice_index=0, order=0,
        ))
        db_session.commit()
        db_session.refresh(exercise)
        classroom_exercise_service.send_exercise(db_session, exercise_id=exercise.id, target="student", student_user_id=user_id)
        db_session.commit()
        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()
        classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(exercise.questions[0].id, 0)],
        )
        db_session.commit()

        items = student_dashboard_service.list_my_deliveries(db_session, user_id)
        assert items[0]["score_pct"] == 100
