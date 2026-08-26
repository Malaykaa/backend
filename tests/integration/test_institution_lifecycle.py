"""Cycle de vie Malayka Institution — archivage et retrait de membres.

Rien ne pouvait etre retire : ni une salle, ni un exercice envoye par erreur, ni
un eleve parti, ni un enseignant qui a quitte l'etablissement. La premiere
erreur d'un enseignant devenait un incident irreparable.

Deux principes verrouilles ici :

- **on archive, on ne supprime pas** — les progressions, les resultats et
  l'historique des rapports survivent ;
- **tout est reversible** — sinon un archivage fait par erreur recreerait
  exactement le probleme qu'il corrige.
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
    ClassroomTeacher,
    MembershipStatus,
    Structure,
    StructureMember,
    StructureMemberRole,
    StructureStatus,
)
from app.services import (
    classroom_course_service,
    classroom_exercise_service,
    structure_access,
    structure_service,
    student_dashboard_service,
)


@pytest.fixture()
def structure(db_session):
    st = Structure(id=uuid.uuid4(), name="Lycee Test", status=StructureStatus.active)
    db_session.add(st)
    db_session.commit()
    return st


@pytest.fixture()
def classroom(db_session, structure):
    room = Classroom(
        id=uuid.uuid4(), structure_id=structure.id, name="Terminale S",
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


def _assign_teacher(db_session, structure, classroom, user_id):
    member = StructureMember(
        id=uuid.uuid4(), structure_id=structure.id, user_id=user_id,
        role=StructureMemberRole.teacher,
    )
    db_session.add(member)
    db_session.flush()
    db_session.add(ClassroomTeacher(
        id=uuid.uuid4(), classroom_id=classroom.id, structure_member_id=member.id,
    ))
    db_session.commit()


def _make_course(db_session, classroom, user_id, title="Cours 1"):
    course = ClassroomCourse(
        id=uuid.uuid4(), classroom_id=classroom.id, title=title, subject="Maths",
        kind=ClassroomCourseKind.course, summary="s", explanation="e",
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


def _make_exercise(db_session, classroom, user_id, title="QCM 1"):
    exercise = ClassroomExercise(
        id=uuid.uuid4(), classroom_id=classroom.id, title=title,
        kind=ClassroomExerciseKind.exercise,
    )
    db_session.add(exercise)
    db_session.flush()
    db_session.add(ClassroomExerciseQuestion(
        id=uuid.uuid4(), exercise_id=exercise.id, prompt="Q1",
        choices=["A", "B"], correct_choice_index=0, points=1, order=0,
    ))
    db_session.commit()
    classroom_exercise_service.send_exercise(
        db_session, exercise_id=exercise.id, target="student", student_user_id=user_id,
    )
    db_session.commit()
    return exercise


class TestArchivageExercice:
    """Seule reponse a un exercice envoye par erreur : son contenu est fige des
    le premier destinataire, il ne peut donc plus etre corrige."""

    def test_exercice_archive_disparait_des_livraisons_eleve(self, db_session, classroom, make_user):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        exercise = _make_exercise(db_session, classroom, user_id)

        avant = student_dashboard_service.list_my_deliveries(db_session, user_id)
        assert any(d["id"] == str(exercise.id) for d in avant)

        classroom_exercise_service.archive_exercise(db_session, exercise.id)
        db_session.commit()

        apres = student_dashboard_service.list_my_deliveries(db_session, user_id)
        assert not any(d["id"] == str(exercise.id) for d in apres)

    def test_archivage_est_reversible(self, db_session, classroom, make_user):
        """Sans reversibilite, un archivage fait par erreur serait lui-meme un
        incident irreparable."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        exercise = _make_exercise(db_session, classroom, user_id)

        classroom_exercise_service.archive_exercise(db_session, exercise.id)
        db_session.commit()
        classroom_exercise_service.archive_exercise(db_session, exercise.id, archived=False)
        db_session.commit()

        apres = student_dashboard_service.list_my_deliveries(db_session, user_id)
        assert any(d["id"] == str(exercise.id) for d in apres)

    def test_les_soumissions_survivent_a_l_archivage(self, db_session, classroom, make_user):
        """On archive, on ne supprime pas : le rapport de difficulte continue de
        s'appuyer sur les reponses deja donnees."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        exercise = _make_exercise(db_session, classroom, user_id)

        classroom_exercise_service.start_submission(
            db_session, exercise_id=exercise.id, user_id=user_id,
        )
        db_session.commit()
        classroom_exercise_service.submit_exercise(
            db_session, exercise_id=exercise.id, user_id=user_id,
            answers=[(q.id, q.correct_choice_index) for q in exercise.questions],
        )
        db_session.commit()

        classroom_exercise_service.archive_exercise(db_session, exercise.id)
        db_session.commit()

        _, submission, _ = classroom_exercise_service.get_my_result(
            db_session, exercise_id=exercise.id, user_id=user_id,
        )
        assert submission.score_pct == 100


class TestArchivageSalleEtCours:
    def test_salle_archivee_masque_ses_contenus_chez_l_eleve(self, db_session, classroom, make_user):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        course = _make_course(db_session, classroom, user_id)

        structure_service.archive_classroom(db_session, classroom.id)
        db_session.commit()

        livraisons = student_dashboard_service.list_my_deliveries(db_session, user_id)
        assert not any(d["id"] == str(course.id) for d in livraisons)

    def test_salle_archivee_reste_accessible_pour_etre_desarchivee(
        self, db_session, structure, classroom, make_user,
    ):
        """L'acces ne filtre PAS les archives : sinon le desarchivage serait
        impossible et l'archivage redeviendrait a sens unique."""
        admin_id, _ = make_user()
        db_session.add(StructureMember(
            id=uuid.uuid4(), structure_id=structure.id, user_id=admin_id,
            role=StructureMemberRole.super_admin,
        ))
        db_session.commit()

        structure_service.archive_classroom(db_session, classroom.id)
        db_session.commit()

        assert classroom.id in structure_access.get_accessible_classroom_ids(db_session, admin_id)
        assert structure_service.list_classrooms(db_session, structure.id) == []
        retrouvees = structure_service.list_classrooms(
            db_session, structure.id, include_archived=True,
        )
        assert [c.id for c in retrouvees] == [classroom.id]

    def test_cours_archive_disparait_de_la_liste_enseignant(self, db_session, classroom, make_user):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        course = _make_course(db_session, classroom, user_id)

        assert len(classroom_course_service.list_courses(db_session, classroom.id)) == 1
        classroom_course_service.archive_course(db_session, course.id)
        db_session.commit()
        assert classroom_course_service.list_courses(db_session, classroom.id) == []


class TestRetraitDeMembres:
    def test_eleve_retire_quitte_la_liste_mais_garde_ses_livraisons(
        self, db_session, classroom, make_user,
    ):
        """Les destinataires sont materialises a l'envoi precisement pour que la
        progression survive au depart (cf. ClassroomCourseRecipient)."""
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)
        course = _make_course(db_session, classroom, user_id)

        structure_service.remove_member(
            db_session, classroom_id=classroom.id, user_id=user_id,
        )
        db_session.commit()

        assert structure_service.list_members(db_session, classroom.id) == []
        livraisons = student_dashboard_service.list_my_deliveries(db_session, user_id)
        assert any(d["id"] == str(course.id) for d in livraisons)

    def test_retrait_d_eleve_est_reversible(self, db_session, classroom, make_user):
        user_id, _ = make_user()
        _enroll(db_session, classroom, user_id)

        structure_service.remove_member(db_session, classroom_id=classroom.id, user_id=user_id)
        db_session.commit()
        structure_service.remove_member(
            db_session, classroom_id=classroom.id, user_id=user_id, removed=False,
        )
        db_session.commit()

        membres = structure_service.list_members(db_session, classroom.id)
        assert [m.user_id for m in membres] == [user_id]

    def test_enseignant_retire_perd_l_acces_a_la_salle(
        self, db_session, structure, classroom, make_user,
    ):
        teacher_id, _ = make_user()
        _assign_teacher(db_session, structure, classroom, teacher_id)
        assert classroom.id in structure_access.get_accessible_classroom_ids(db_session, teacher_id)

        structure_service.remove_classroom_teacher(
            db_session, classroom_id=classroom.id, user_id=teacher_id,
        )
        db_session.commit()

        assert classroom.id not in structure_access.get_accessible_classroom_ids(
            db_session, teacher_id,
        )

    def test_retrait_d_enseignant_est_reversible(
        self, db_session, structure, classroom, make_user,
    ):
        teacher_id, _ = make_user()
        _assign_teacher(db_session, structure, classroom, teacher_id)

        structure_service.remove_classroom_teacher(
            db_session, classroom_id=classroom.id, user_id=teacher_id,
        )
        db_session.commit()
        structure_service.remove_classroom_teacher(
            db_session, classroom_id=classroom.id, user_id=teacher_id, removed=False,
        )
        db_session.commit()

        assert classroom.id in structure_access.get_accessible_classroom_ids(db_session, teacher_id)
