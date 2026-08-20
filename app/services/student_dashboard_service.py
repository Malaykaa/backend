"""student_dashboard_service — vue agrégée des cours/exercices reçus par un élève.

Corrige un vrai trou du parcours élève : jusqu'ici, un cours ou un exercice
n'était accessible que via le lien de la notification qui l'a annoncé — aucune
page ne listait l'ensemble de ce qu'un élève a reçu. Combine
ClassroomCourseRecipient et ClassroomExerciseRecipient (deux tables distinctes,
cf. structure_access.py sur pourquoi le contenu envoyé par une structure ne se
mélange jamais à l'activité personnelle) en une seule liste triée par date.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.structure import (
    Classroom,
    ClassroomCourse,
    ClassroomCourseRecipient,
    ClassroomCourseStepProgress,
    ClassroomExercise,
    ClassroomExerciseRecipient,
    ClassroomExerciseSubmission,
    ExerciseSubmissionStatus,
)


def list_my_deliveries(db: Session, user_id: uuid.UUID) -> list[dict]:
    items: list[dict] = []

    course_rows = db.execute(
        select(ClassroomCourseRecipient, ClassroomCourse, Classroom)
        .join(ClassroomCourse, ClassroomCourseRecipient.course_id == ClassroomCourse.id)
        .join(Classroom, ClassroomCourse.classroom_id == Classroom.id)
        .where(ClassroomCourseRecipient.user_id == user_id)
    ).all()
    for recipient, course, classroom in course_rows:
        progress_rows = db.execute(
            select(ClassroomCourseStepProgress.status).where(
                ClassroomCourseStepProgress.recipient_id == recipient.id
            )
        ).scalars().all()
        total = len(progress_rows)
        done = sum(1 for s in progress_rows if s.value == "done")
        items.append({
            "kind": "evolution_plan" if course.kind.value == "evolution_plan" else "course",
            "id": str(course.id),
            "title": course.title,
            "classroom_name": classroom.name,
            "created_at": recipient.created_at,
            "completion_pct": round(done / total * 100) if total else 0,
            "score_pct": None,
        })

    exercise_rows = db.execute(
        select(ClassroomExerciseRecipient, ClassroomExercise, Classroom)
        .join(ClassroomExercise, ClassroomExerciseRecipient.exercise_id == ClassroomExercise.id)
        .join(Classroom, ClassroomExercise.classroom_id == Classroom.id)
        .where(ClassroomExerciseRecipient.user_id == user_id)
    ).all()
    for recipient, exercise, classroom in exercise_rows:
        submissions = db.execute(
            select(ClassroomExerciseSubmission).where(
                ClassroomExerciseSubmission.recipient_id == recipient.id,
                ClassroomExerciseSubmission.status == ExerciseSubmissionStatus.submitted,
            )
        ).scalars().all()
        best_score = max((s.score_pct or 0 for s in submissions), default=None)
        items.append({
            "kind": exercise.kind.value,  # "exercise" | "evaluation"
            "id": str(exercise.id),
            "title": exercise.title,
            "classroom_name": classroom.name,
            "created_at": recipient.created_at,
            "completion_pct": 100 if submissions else 0,
            "score_pct": best_score,
        })

    items.sort(key=lambda i: i["created_at"], reverse=True)
    return items
