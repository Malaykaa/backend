"""Soumission, correction déterministe, règle de tentatives et détection de
difficulté pour les exercices/évaluations QCM.

Construit les objets directement en base (pas via create_exercise, qui appelle le
LLM) — ces tests verrouillent la logique de classroom_exercise_service, pas la
génération (couverte séparément par tests/unit/test_exercise_generation.py).
"""

from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
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
from app.services import classroom_exercise_service


@pytest.fixture()
def classroom(db_session):
    """Crée une Structure active + une Classroom, retourne la Classroom."""
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
    """Factory : inscrit un étudiant (déjà créé via make_user) dans la classroom."""
    def _enroll(user_id: uuid.UUID) -> None:
        db_session.add(ClassroomMembership(
            id=uuid.uuid4(), classroom_id=classroom.id, user_id=user_id,
            requested_first_name="Test", requested_last_name="Student",
            status=MembershipStatus.accepted,
        ))
        db_session.commit()
    return _enroll


def _make_exercise(db_session, classroom, *, kind=ClassroomExerciseKind.exercise, n_questions=2) -> ClassroomExercise:
    exercise = ClassroomExercise(
        id=uuid.uuid4(), classroom_id=classroom.id, title="QCM dérivées",
        kind=kind, topic_hint="dérivées",
    )
    db_session.add(exercise)
    db_session.flush()
    for i in range(n_questions):
        db_session.add(ClassroomExerciseQuestion(
            id=uuid.uuid4(), exercise_id=exercise.id,
            prompt=f"Question {i}", choices=["A", "B", "C"], correct_choice_index=1,
            points=1, order=i, topic_tag="dérivées simples",
        ))
    db_session.commit()
    db_session.refresh(exercise)
    return exercise


class TestCorrectionDeterministe:
    def test_score_calcule_par_comparaison_directe(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom, n_questions=2)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()

        submission = classroom_exercise_service.start_submission(
            db_session, exercise_id=exercise.id, user_id=user_id,
        )
        db_session.commit()

        q0, q1 = exercise.questions[0], exercise.questions[1]
        result = classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(q0.id, 1), (q1.id, 0)],  # bonne réponse puis mauvaise
        )
        db_session.commit()

        assert result.score_points == 1
        assert result.max_points == 2
        assert result.score_pct == 50
        assert result.status.value == "submitted"

    def test_reponse_non_donnee_est_marquee_incorrecte(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom, n_questions=1)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()

        result = classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(exercise.questions[0].id, None)],
        )
        assert result.score_pct == 0


class TestReglesDeTentatives:
    def test_exercice_autorise_plusieurs_tentatives(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom, kind=ClassroomExerciseKind.exercise)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()

        s1 = classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()
        classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(q.id, q.correct_choice_index) for q in exercise.questions],
        )
        db_session.commit()

        s2 = classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()

        assert s2.attempt_number == s1.attempt_number + 1

    def test_evaluation_refuse_une_deuxieme_tentative(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom, kind=ClassroomExerciseKind.evaluation)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()

        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()
        classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(q.id, q.correct_choice_index) for q in exercise.questions],
        )
        db_session.commit()

        with pytest.raises(ConflictError):
            classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)

    def test_tentative_en_cours_est_reutilisee_pas_recreee(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()

        s1 = classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        s2 = classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        assert s1.id == s2.id

    def test_exercice_non_envoye_refuse_le_demarrage(self, db_session, classroom, make_user):
        user_id, _ = make_user()
        exercise = _make_exercise(db_session, classroom)
        with pytest.raises(ForbiddenError):
            classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)


class TestIntegriteDuCorrige:
    """La clé de correction ne doit jamais être atteignable avant soumission.

    get_my_result ne filtrait pas sur le statut de la tentative : celle créée par
    start_submission (in_progress, réponses pré-créées) remontait telle quelle, et
    la réponse du routeur porte correct_choice_index + explanation. Un élève
    pouvait donc enchaîner start → my-result → submit et rendre une évaluation
    notée à 100 % sans avoir répondu.
    """

    def test_tentative_en_cours_nexpose_pas_le_corrige(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom, kind=ClassroomExerciseKind.evaluation)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()

        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()

        # Avant le correctif : renvoyait la tentative in_progress et ses réponses.
        with pytest.raises(NotFoundError):
            classroom_exercise_service.get_my_result(
                db_session, exercise_id=exercise.id, user_id=user_id,
            )

    def test_resultat_reste_accessible_apres_soumission(self, db_session, classroom, enroll, make_user):
        """Contre-épreuve : le correctif ne casse pas le parcours normal."""
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()

        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()
        classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(q.id, q.correct_choice_index) for q in exercise.questions],
        )
        db_session.commit()

        _, submission, answers = classroom_exercise_service.get_my_result(
            db_session, exercise_id=exercise.id, user_id=user_id,
        )
        assert submission.score_pct == 100
        assert len(answers) == len(exercise.questions)

    def test_nouvelle_tentative_ne_masque_pas_le_resultat_precedent(
        self, db_session, classroom, enroll, make_user,
    ):
        """Un exercice autorise plusieurs tentatives. En rouvrir une ne doit pas
        rendre inaccessible le résultat déjà obtenu — le tri par numéro de
        tentative décroissant retiendrait sinon la nouvelle, encore en cours."""
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom, kind=ClassroomExerciseKind.exercise)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()

        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()
        classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(q.id, q.correct_choice_index) for q in exercise.questions],
        )
        db_session.commit()

        # L'élève rouvre une seconde tentative sans la soumettre.
        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()

        _, submission, _ = classroom_exercise_service.get_my_result(
            db_session, exercise_id=exercise.id, user_id=user_id,
        )
        assert submission.attempt_number == 1
        assert submission.score_pct == 100


class TestDetectionDifficulte:
    def test_classe_sans_soumission_retourne_insufficient_data(self, db_session, classroom):
        report = classroom_exercise_service.get_classroom_difficulty_report(db_session, classroom_id=classroom.id)
        assert report["insufficient_data"] is True
        assert report["students"] == []

    def test_etudiant_avec_echecs_repetes_est_flague(self, db_session, classroom, enroll, make_user):
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom, n_questions=3)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()
        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()
        # 2 mauvaises réponses sur 3 (toutes topic_tag="dérivées simples") → wrong_rate = 0.67 >= seuil
        classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[
                (exercise.questions[0].id, 0),  # faux (correct=1)
                (exercise.questions[1].id, 0),  # faux
                (exercise.questions[2].id, 1),  # correct
            ],
        )
        db_session.commit()

        report = classroom_exercise_service.get_classroom_difficulty_report(db_session, classroom_id=classroom.id)
        assert report["insufficient_data"] is False
        student = report["students"][0]
        assert student["user_id"] == user_id
        assert any(f["topic_tag"] == "dérivées simples" for f in student["flagged_topics"])

    def test_un_seul_echec_isole_ne_flague_pas(self, db_session, classroom, enroll, make_user):
        """Seuil de taille d'échantillon (>=2 questions vues) : un coup de malchance
        isolé sur une seule question ne doit pas suffire à flaguer une notion."""
        user_id, _ = make_user()
        enroll(user_id)
        exercise = _make_exercise(db_session, classroom, n_questions=1)
        classroom_exercise_service.send_exercise(
            db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
        )
        db_session.commit()
        classroom_exercise_service.start_submission(db_session, exercise_id=exercise.id, user_id=user_id)
        db_session.commit()
        classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(exercise.questions[0].id, 0)],  # faux, mais une seule question vue sur ce topic
        )
        db_session.commit()

        report = classroom_exercise_service.get_classroom_difficulty_report(db_session, classroom_id=classroom.id)
        student = report["students"][0]
        assert student["flagged_topics"] == []
