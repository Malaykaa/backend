"""Du resultat d'evaluation vers une suite concrete pour l'eleve.

Deux maillons manquaient entre "l'eleve a rate" et "l'eleve sait quoi faire" :

1. Le diagnostic par notion existait, mais uniquement derriere
   _require_classroom_admin : le systeme savait qu'un eleve bloquait sur une
   notion precise et ne le lui disait jamais. Il ne voyait qu'un score.

2. Le plan d'evolution ne lisait QUE les cases de cours cochees. Un eleve ayant
   tout coche en obtenant 20 % a chaque evaluation produisait exactement le meme
   plan qu'un eleve a 100 %.
"""

from __future__ import annotations

import uuid

import pytest

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


def _echouer_exercice(db_session, classroom, user_id, *, topic, subject="Maths", n=3):
    """Envoie un exercice sur une notion et le fait rater entierement."""
    exercise = ClassroomExercise(
        id=uuid.uuid4(), classroom_id=classroom.id, title=f"QCM {topic}",
        subject=subject, kind=ClassroomExerciseKind.exercise,
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
    classroom_exercise_service.submit_exercise(
        db_session, exercise_id=exercise.id, user_id=user_id,
        answers=[(q.id, 1) for q in exercise.questions],  # tout faux
    )
    db_session.commit()
    return exercise


def _cours_avec_etape(db_session, classroom, user_id, subject="Maths"):
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


class TestDiagnosticVisibleParLEleve:
    def test_l_eleve_voit_ses_propres_notions_en_difficulte(
        self, db_session, classroom, make_user,
    ):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _echouer_exercice(db_session, classroom, user_id, topic="derivees composees")

        items = classroom_exercise_service.get_my_difficulty(db_session, user_id=user_id)

        assert len(items) == 1
        assert items[0]["classroom_name"] == "Terminale S"
        assert items[0]["avg_score_pct"] == 0
        tags = [f["topic_tag"] for f in items[0]["flagged_topics"]]
        assert "derivees composees" in tags

    def test_ne_expose_jamais_les_donnees_d_un_autre_eleve(
        self, db_session, classroom, make_user,
    ):
        """La reponse ne doit contenir que celui qui la demande — et surtout pas
        le champ `topics` du rapport de classe, qui agrege les autres."""
        moi, _ = make_user()
        autre, _ = make_user()
        for uid in (moi, autre):
            _enroll(db_session, classroom, uid)
        _echouer_exercice(db_session, classroom, autre, topic="limites de suites")

        items = classroom_exercise_service.get_my_difficulty(db_session, user_id=moi)

        assert items == [], "un eleve sans soumission ne doit rien recevoir"
        for item in classroom_exercise_service.get_my_difficulty(db_session, user_id=autre):
            assert "topics" not in item
            assert "user_id" not in item

    def test_un_echec_isole_ne_declenche_pas_d_alerte(self, db_session, classroom, make_user):
        """Le seuil du rapport enseignant s'applique aussi ici : on ne declare pas
        une notion "non maitrisee" sur une seule question ratee, a plus forte
        raison quand c'est l'eleve qui le lit."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _echouer_exercice(db_session, classroom, user_id, topic="notion rare", n=1)

        items = classroom_exercise_service.get_my_difficulty(db_session, user_id=user_id)
        tags = [f["topic_tag"] for it in items for f in it["flagged_topics"]]
        assert "notion rare" not in tags


class TestPlanTientCompteDesResultats:
    def test_les_notions_ratees_alimentent_le_plan(self, db_session, classroom, make_user):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _cours_avec_etape(db_session, classroom, user_id, subject="Maths")
        _echouer_exercice(db_session, classroom, user_id, topic="derivees composees", subject="Maths")

        resume = classroom_course_service._build_progress_summary(
            db_session, classroom_id=classroom.id, user_id=user_id, subject="Maths",
        )

        assert "derivees composees" in resume
        assert "Résultats aux exercices" in resume
        assert "moyenne 0%" in resume

    def test_le_plan_ne_melange_pas_les_matieres(self, db_session, classroom, make_user):
        """Le plan est genere PAR MATIERE : les resultats d'une autre matiere ne
        doivent pas s'y glisser, sinon l'agent raisonne sur des donnees fausses."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _cours_avec_etape(db_session, classroom, user_id, subject="Maths")
        _echouer_exercice(
            db_session, classroom, user_id, topic="conjugaison", subject="Français",
        )

        resume = classroom_course_service._build_progress_summary(
            db_session, classroom_id=classroom.id, user_id=user_id, subject="Maths",
        )

        assert "conjugaison" not in resume

    def test_le_rapport_enseignant_reste_toutes_matieres(self, db_session, classroom, make_user):
        """Contre-epreuve : le filtre par matiere est optionnel, le tableau de
        bord enseignant ne doit pas avoir change de comportement."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        _echouer_exercice(db_session, classroom, user_id, topic="derivees", subject="Maths")
        _echouer_exercice(db_session, classroom, user_id, topic="conjugaison", subject="Français")

        report = classroom_exercise_service.get_classroom_difficulty_report(
            db_session, classroom_id=classroom.id,
        )
        tags = [t["topic_tag"] for t in report["topics"]]
        assert "derivees" in tags and "conjugaison" in tags
