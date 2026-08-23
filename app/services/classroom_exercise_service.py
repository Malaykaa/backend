"""classroom_exercise_service — exercices/évaluations QCM envoyés par un enseignant.

Miroir de classroom_course_service.py (même structure classroom→contenu→destinataires
matérialisés→progression), avec kind pour distinguer un exercice d'entraînement
(tentatives illimitées) d'une évaluation notée (une seule tentative). La génération
passe par exercise_generation.py (appel LLM structuré JSON), pas par un SpecializedAgent.

Correction : entièrement déterministe (comparaison à correct_choice_index), aucun
appel LLM à la soumission — décision produit verrouillée pour la V1 (QCM uniquement).
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.models.chat import MessageRole
from app.models.notification import UserNotification
from app.models.structure import (
    ClassroomCourse,
    ClassroomExercise,
    ClassroomExerciseAnswer,
    ClassroomExerciseKind,
    ClassroomExerciseQuestion,
    ClassroomExerciseRecipient,
    ClassroomExerciseSubmission,
    ClassroomMembership,
    ExerciseSubmissionStatus,
    MembershipStatus,
)
from app.models.user import User
from app.repositories.chat_repo import ChatRepository
from app.schemas.exercise_generation import GeneratedExercise
from app.services import exercise_generation

logger = logging.getLogger(__name__)


async def create_exercise(
    db: Session,
    *,
    classroom_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    title: str,
    topic_hint: str,
    subject: str | None = None,
    kind: ClassroomExerciseKind = ClassroomExerciseKind.exercise,
    question_count: int = 8,
    source_course_id: uuid.UUID | None = None,
) -> ClassroomExercise:
    if not title.strip() or not topic_hint.strip():
        raise BadRequestError("Le titre et la consigne sont requis.")

    source_content: str | None = None
    if source_course_id:
        source_course = db.get(ClassroomCourse, source_course_id)
        if not source_course or source_course.classroom_id != classroom_id:
            raise NotFoundError("Cours source")
        source_content = source_course.explanation

    generated: GeneratedExercise = await exercise_generation.generate_exercise(
        title=title, topic_hint=topic_hint, subject=subject, kind=kind,
        question_count=question_count, source_content=source_content,
    )

    exercise = ClassroomExercise(
        id=uuid.uuid4(),
        classroom_id=classroom_id,
        created_by_user_id=created_by_user_id,
        title=generated.title.strip() or title.strip(),
        subject=subject.strip() if subject else None,
        kind=kind,
        topic_hint=topic_hint.strip(),
        instructions=generated.instructions,
        source_course_id=source_course_id,
    )
    db.add(exercise)
    db.flush()

    for i, q in enumerate(generated.questions):
        db.add(ClassroomExerciseQuestion(
            id=uuid.uuid4(), exercise_id=exercise.id, prompt=q.prompt, choices=q.choices,
            correct_choice_index=q.correct_choice_index, explanation=q.explanation,
            points=1, order=i, topic_tag=q.topic_tag,
        ))
    db.flush()

    return exercise


def update_exercise_questions(
    db: Session, *, exercise_id: uuid.UUID, questions: list,
) -> ClassroomExercise:
    """Édite les questions générées avant envoi — interdit dès qu'un destinataire
    existe (une fois envoyé, le contenu est figé, comme pour un cours)."""
    exercise = get_exercise(db, exercise_id)

    has_recipients = db.execute(
        select(ClassroomExerciseRecipient.id).where(
            ClassroomExerciseRecipient.exercise_id == exercise_id
        )
    ).scalar_one_or_none()
    if has_recipients:
        raise ConflictError("Impossible de modifier un exercice déjà envoyé.")

    if not questions:
        raise BadRequestError("L'exercice doit contenir au moins une question.")

    for q in questions:
        if not (2 <= len(q.choices) <= 6):
            raise BadRequestError("Chaque question doit avoir entre 2 et 6 choix.")
        if not (0 <= q.correct_choice_index < len(q.choices)):
            raise BadRequestError("correct_choice_index hors bornes pour une question.")

    for old_q in list(exercise.questions):
        db.delete(old_q)
    db.flush()

    for i, q in enumerate(questions):
        db.add(ClassroomExerciseQuestion(
            id=uuid.uuid4(), exercise_id=exercise.id, prompt=q.prompt, choices=q.choices,
            correct_choice_index=q.correct_choice_index, explanation=q.explanation,
            points=q.points if q.points else 1, order=i, topic_tag=q.topic_tag,
        ))
    db.flush()
    db.refresh(exercise)
    return exercise


def list_exercises(
    db: Session, classroom_id: uuid.UUID, kind: ClassroomExerciseKind | None = None,
) -> list[ClassroomExercise]:
    stmt = select(ClassroomExercise).where(ClassroomExercise.classroom_id == classroom_id)
    if kind:
        stmt = stmt.where(ClassroomExercise.kind == kind)
    stmt = stmt.order_by(ClassroomExercise.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def get_exercise(db: Session, exercise_id: uuid.UUID) -> ClassroomExercise:
    exercise = db.get(ClassroomExercise, exercise_id)
    if not exercise:
        raise NotFoundError("Exercice")
    return exercise


def _notification_url(thread_id: uuid.UUID) -> str:
    return f"/app/chat/{thread_id}"


def _materialize_exercise_recipient(
    db: Session, exercise: ClassroomExercise, user_id: uuid.UUID,
) -> ClassroomExerciseRecipient:
    settings = get_settings()
    take_url = f"{settings.frontend_url.rstrip('/')}/classrooms/exercises/{exercise.id}"
    kind_label = "évaluation" if exercise.kind == ClassroomExerciseKind.evaluation else "exercice"
    message_content = (
        f"Nouvel {kind_label} : **{exercise.title}**\n\n"
        + (f"{exercise.instructions}\n\n" if exercise.instructions else "")
        + f"---\n[Faire l'exercice →]({take_url})"
    )

    chat_repo = ChatRepository(db)
    thread = chat_repo.create_thread(user_id=user_id, title=exercise.title, goal_id=None)
    chat_repo.add_message(
        thread_id=thread.id, role=MessageRole.assistant, content=message_content,
        payload={"agent_id": "classroom_exercise", "exercise_id": str(exercise.id)},
    )

    recipient = ClassroomExerciseRecipient(
        id=uuid.uuid4(), exercise_id=exercise.id, user_id=user_id, chat_thread_id=thread.id,
    )
    db.add(recipient)
    db.flush()

    notif_title = f"Nouvelle évaluation : {exercise.title}" if exercise.kind == ClassroomExerciseKind.evaluation \
        else f"Nouvel exercice : {exercise.title}"
    notif_url = _notification_url(thread.id)
    db.add(UserNotification(
        user_id=user_id, offer_id=None,
        offer_title=notif_title, offer_url=notif_url,
        offer_type="exercise_assigned", score_pct=None, seen=False,
    ))
    from app.services.push_service import send_push
    send_push(db, user_id=user_id, title=notif_title, url=notif_url)

    db.flush()
    return recipient


def send_exercise(
    db: Session, *, exercise_id: uuid.UUID, target: str, student_user_id: uuid.UUID | None,
) -> list[ClassroomExerciseRecipient]:
    """Idempotent : miroir exact de classroom_course_service.send_course."""
    exercise = get_exercise(db, exercise_id)

    if target == "classroom":
        recipient_user_ids = set(
            db.execute(
                select(ClassroomMembership.user_id).where(
                    ClassroomMembership.classroom_id == exercise.classroom_id,
                    ClassroomMembership.status == MembershipStatus.accepted,
                )
            ).scalars().all()
        )
    elif target == "student":
        if not student_user_id:
            raise BadRequestError("student_user_id requis pour un envoi individuel.")
        is_member = db.execute(
            select(ClassroomMembership.id).where(
                ClassroomMembership.classroom_id == exercise.classroom_id,
                ClassroomMembership.user_id == student_user_id,
                ClassroomMembership.status == MembershipStatus.accepted,
            )
        ).scalar_one_or_none()
        if not is_member:
            raise NotFoundError("Étudiant dans cette Classroom")
        recipient_user_ids = {student_user_id}
    else:
        raise BadRequestError("target doit être 'classroom' ou 'student'.")

    existing_user_ids = set(
        db.execute(
            select(ClassroomExerciseRecipient.user_id).where(
                ClassroomExerciseRecipient.exercise_id == exercise.id
            )
        ).scalars().all()
    )
    new_user_ids = recipient_user_ids - existing_user_ids

    return [_materialize_exercise_recipient(db, exercise, user_id) for user_id in new_user_ids]


def _get_exercise_recipient(
    db: Session, exercise_id: uuid.UUID, user_id: uuid.UUID,
) -> ClassroomExerciseRecipient:
    recipient = db.execute(
        select(ClassroomExerciseRecipient).where(
            ClassroomExerciseRecipient.exercise_id == exercise_id,
            ClassroomExerciseRecipient.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not recipient:
        raise ForbiddenError("Cet exercice ne t'a pas été envoyé.")
    return recipient


def get_exercise_for_student(db: Session, *, exercise_id: uuid.UUID, user_id: uuid.UUID) -> ClassroomExercise:
    """Vérifie que l'exercice a bien été envoyé à cet utilisateur, puis le retourne
    (jamais correct_choice_index/explanation — c'est au routeur de construire la
    réponse student-safe, pas à ce service de filtrer les champs)."""
    _get_exercise_recipient(db, exercise_id, user_id)
    return get_exercise(db, exercise_id)


def start_submission(
    db: Session, *, exercise_id: uuid.UUID, user_id: uuid.UUID,
) -> ClassroomExerciseSubmission:
    exercise = get_exercise(db, exercise_id)
    recipient = _get_exercise_recipient(db, exercise_id, user_id)

    existing = list(
        db.execute(
            select(ClassroomExerciseSubmission)
            .where(ClassroomExerciseSubmission.recipient_id == recipient.id)
            .order_by(ClassroomExerciseSubmission.attempt_number.desc())
        ).scalars().all()
    )

    in_progress = next((s for s in existing if s.status == ExerciseSubmissionStatus.in_progress), None)
    if in_progress:
        return in_progress

    if exercise.kind == ClassroomExerciseKind.evaluation and any(
        s.status == ExerciseSubmissionStatus.submitted for s in existing
    ):
        raise ConflictError("Cette évaluation a déjà été soumise — une seule tentative autorisée.")

    attempt_number = (existing[0].attempt_number + 1) if existing else 1
    submission = ClassroomExerciseSubmission(
        id=uuid.uuid4(), recipient_id=recipient.id, attempt_number=attempt_number,
        status=ExerciseSubmissionStatus.in_progress,
    )
    db.add(submission)
    db.flush()

    for question in exercise.questions:
        db.add(ClassroomExerciseAnswer(
            id=uuid.uuid4(), submission_id=submission.id, question_id=question.id,
            selected_choice_index=None, is_correct=False, points_earned=0,
        ))
    db.flush()
    return submission


def submit_exercise(
    db: Session, *, exercise_id: uuid.UUID, user_id: uuid.UUID,
    answers: list[tuple[uuid.UUID, int | None]],
) -> ClassroomExerciseSubmission:
    """Correction déterministe : aucun appel LLM, comparaison directe à
    correct_choice_index. C'est toute la capacité 'corriger' de la V1."""
    exercise = get_exercise(db, exercise_id)
    recipient = _get_exercise_recipient(db, exercise_id, user_id)

    submission = db.execute(
        select(ClassroomExerciseSubmission).where(
            ClassroomExerciseSubmission.recipient_id == recipient.id,
            ClassroomExerciseSubmission.status == ExerciseSubmissionStatus.in_progress,
        )
    ).scalar_one_or_none()
    if not submission:
        raise NotFoundError("Tentative en cours — appelle start_submission d'abord.")

    questions_by_id = {q.id: q for q in exercise.questions}
    answers_by_question = {
        a.question_id: a
        for a in db.execute(
            select(ClassroomExerciseAnswer).where(
                ClassroomExerciseAnswer.submission_id == submission.id
            )
        ).scalars().all()
    }

    for question_id, selected_choice_index in answers:
        question = questions_by_id.get(question_id)
        answer = answers_by_question.get(question_id)
        if not question or not answer:
            continue
        answer.selected_choice_index = selected_choice_index
        answer.is_correct = (
            selected_choice_index is not None and selected_choice_index == question.correct_choice_index
        )
        answer.points_earned = question.points if answer.is_correct else 0

    max_points = sum(q.points for q in exercise.questions)
    score_points = sum(a.points_earned for a in answers_by_question.values())
    submission.score_points = score_points
    submission.max_points = max_points
    submission.score_pct = round(score_points / max_points * 100) if max_points else 0
    submission.status = ExerciseSubmissionStatus.submitted
    submission.submitted_at = datetime.now(timezone.utc)
    db.flush()
    return submission


def get_my_result(
    db: Session, *, exercise_id: uuid.UUID, user_id: uuid.UUID, attempt_number: int | None = None,
) -> tuple[ClassroomExercise, ClassroomExerciseSubmission, list[ClassroomExerciseAnswer]]:
    exercise = get_exercise(db, exercise_id)
    recipient = _get_exercise_recipient(db, exercise_id, user_id)

    # Ne remonter QUE des tentatives déjà soumises. Sans ce filtre, la tentative
    # créée par start_submission (statut in_progress, réponses pré-créées) était
    # retournée telle quelle — et la réponse du routeur porte correct_choice_index
    # et explanation pour chaque question. Un élève pouvait donc enchaîner
    # start → my-result → submit et obtenir le corrigé avant de répondre, ce qui
    # vidait de son sens une évaluation notée à tentative unique.
    stmt = select(ClassroomExerciseSubmission).where(
        ClassroomExerciseSubmission.recipient_id == recipient.id,
        ClassroomExerciseSubmission.status == ExerciseSubmissionStatus.submitted,
    )
    if attempt_number is not None:
        stmt = stmt.where(ClassroomExerciseSubmission.attempt_number == attempt_number)
    else:
        stmt = stmt.order_by(ClassroomExerciseSubmission.attempt_number.desc())
    submission = db.execute(stmt).scalars().first()
    if not submission:
        raise NotFoundError("Tentative")

    answers = list(
        db.execute(
            select(ClassroomExerciseAnswer).where(
                ClassroomExerciseAnswer.submission_id == submission.id
            )
        ).scalars().all()
    )
    return exercise, submission, answers


def get_my_attempts(
    db: Session, *, exercise_id: uuid.UUID, user_id: uuid.UUID,
) -> list[ClassroomExerciseSubmission]:
    recipient = _get_exercise_recipient(db, exercise_id, user_id)
    return list(
        db.execute(
            select(ClassroomExerciseSubmission)
            .where(
                ClassroomExerciseSubmission.recipient_id == recipient.id,
                ClassroomExerciseSubmission.status == ExerciseSubmissionStatus.submitted,
            )
            .order_by(ClassroomExerciseSubmission.attempt_number)
        ).scalars().all()
    )


def get_results_matrix(
    db: Session, *, exercise_id: uuid.UUID,
) -> list[tuple[ClassroomExerciseRecipient, ClassroomExerciseSubmission | None]]:
    """Vue enseignant : meilleure tentative soumise par destinataire (ou None si
    pas encore tenté)."""
    exercise = get_exercise(db, exercise_id)
    recipients = list(
        db.execute(
            select(ClassroomExerciseRecipient).where(
                ClassroomExerciseRecipient.exercise_id == exercise_id
            )
        ).scalars().all()
    )
    result = []
    for recipient in recipients:
        submissions = list(
            db.execute(
                select(ClassroomExerciseSubmission).where(
                    ClassroomExerciseSubmission.recipient_id == recipient.id,
                    ClassroomExerciseSubmission.status == ExerciseSubmissionStatus.submitted,
                )
            ).scalars().all()
        )
        best = max(submissions, key=lambda s: s.score_pct or 0, default=None) \
            if exercise.kind == ClassroomExerciseKind.exercise else (submissions[0] if submissions else None)
        result.append((recipient, best))
    return result


# ── Détection de difficulté ───────────────────────────────────────────────────
# Construite strictement sur des soumissions réelles (jamais sur des tables vides
# — cf. insufficient_data ci-dessous) : un score moyen bas + des notions flaguées
# répétées, pas juste un % de complétion d'étapes cochées.

_MIN_WRONG_RATE = 0.5
_MIN_QUESTIONS_SEEN = 2


def _normalize_topic(tag: str) -> str:
    """Clé de regroupement des notions, insensible à la casse, aux accents et à
    la ponctuation.

    topic_tag est un texte libre produit par le LLM à chaque génération
    d'exercice, sans vocabulaire imposé : « Dérivées composées », « dérivées
    composées » et « Derivees composees » désignent la même notion mais
    formaient trois entrées distinctes dans le rapport. La cohérence tenait au
    sein d'un même exercice, et se délitait dès qu'on en comparait plusieurs —
    c'est-à-dire précisément ce que ce rapport calcule.

    Le libellé d'origine reste affiché (cf. topic_labels) : on ne normalise que
    la clé de regroupement, jamais ce que voit l'enseignant.
    """
    folded = unicodedata.normalize("NFKD", tag)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[^\w\s]", " ", folded.lower())
    return re.sub(r"\s+", " ", folded).strip()


def get_classroom_difficulty_report(db: Session, *, classroom_id: uuid.UUID) -> dict:
    rows = db.execute(
        select(
            ClassroomExerciseRecipient.user_id,
            ClassroomExerciseQuestion.topic_tag,
            ClassroomExerciseAnswer.is_correct,
        )
        .join(ClassroomExerciseSubmission, ClassroomExerciseAnswer.submission_id == ClassroomExerciseSubmission.id)
        .join(ClassroomExerciseRecipient, ClassroomExerciseSubmission.recipient_id == ClassroomExerciseRecipient.id)
        .join(ClassroomExerciseQuestion, ClassroomExerciseAnswer.question_id == ClassroomExerciseQuestion.id)
        .join(ClassroomExercise, ClassroomExerciseRecipient.exercise_id == ClassroomExercise.id)
        .where(
            ClassroomExercise.classroom_id == classroom_id,
            ClassroomExerciseSubmission.status == ExerciseSubmissionStatus.submitted,
        )
    ).all()

    if not rows:
        return {"students": [], "topics": [], "insufficient_data": True}

    by_student: dict[uuid.UUID, dict[str, list[bool]]] = {}
    by_topic: dict[str, list[bool]] = {}
    # Clé normalisée → libellés d'origine rencontrés, pour réafficher à
    # l'enseignant l'orthographe la plus fréquente plutôt que la forme normalisée.
    topic_labels: dict[str, Counter] = {}
    for user_id, topic_tag, is_correct in rows:
        raw = (topic_tag or "").strip() or "Non catégorisé"
        topic = _normalize_topic(raw) or "non categorise"
        topic_labels.setdefault(topic, Counter())[raw] += 1
        by_student.setdefault(user_id, {}).setdefault(topic, []).append(is_correct)
        by_topic.setdefault(topic, []).append(is_correct)

    def _label(key: str) -> str:
        counter = topic_labels.get(key)
        return counter.most_common(1)[0][0] if counter else key

    # Score moyen et tendance par étudiant, à partir des soumissions (pas des réponses brutes).
    student_submissions: dict[uuid.UUID, list[ClassroomExerciseSubmission]] = {}
    submission_rows = db.execute(
        select(ClassroomExerciseRecipient.user_id, ClassroomExerciseSubmission)
        .join(ClassroomExerciseSubmission, ClassroomExerciseSubmission.recipient_id == ClassroomExerciseRecipient.id)
        .where(
            ClassroomExerciseRecipient.exercise_id.in_(
                select(ClassroomExercise.id).where(ClassroomExercise.classroom_id == classroom_id)
            ),
            ClassroomExerciseSubmission.status == ExerciseSubmissionStatus.submitted,
        )
    ).all()
    for user_id, submission in submission_rows:
        student_submissions.setdefault(user_id, []).append(submission)

    students = []
    for user_id, topics in by_student.items():
        flagged = []
        for topic, results in topics.items():
            wrong_rate = 1 - (sum(results) / len(results))
            if wrong_rate >= _MIN_WRONG_RATE and len(results) >= _MIN_QUESTIONS_SEEN:
                flagged.append({
                    "topic_tag": _label(topic), "wrong_rate": round(wrong_rate, 2),
                    "questions_seen": len(results),
                })

        submissions = sorted(student_submissions.get(user_id, []), key=lambda s: s.attempt_number)
        avg_score_pct = round(sum(s.score_pct or 0 for s in submissions) / len(submissions)) if submissions else 0
        trend = None
        multi_attempt = [s for s in submissions if s.score_pct is not None]
        if len(multi_attempt) >= 2:
            delta = multi_attempt[-1].score_pct - multi_attempt[0].score_pct
            trend = "improving" if delta > 5 else "declining" if delta < -5 else "flat"

        user = db.get(User, user_id)
        user_name = None
        if user and user.profile:
            parts = [user.profile.first_name, user.profile.last_name]
            user_name = " ".join(p for p in parts if p) or None

        students.append({
            "user_id": user_id, "user_name": user_name, "avg_score_pct": avg_score_pct,
            "flagged_topics": flagged, "trend": trend,
        })

    topics_report = [
        {
            "topic_tag": _label(topic),
            "class_success_rate": round(sum(results) / len(results) * 100),
            "students_flagged_count": sum(
                1 for s in students if any(f["topic_tag"] == _label(topic) for f in s["flagged_topics"])
            ),
        }
        for topic, results in by_topic.items()
    ]
    topics_report.sort(key=lambda t: t["class_success_rate"])

    return {"students": students, "topics": topics_report, "insufficient_data": False}


def get_student_difficulty_detail(db: Session, *, classroom_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    report = get_classroom_difficulty_report(db, classroom_id=classroom_id)
    if report["insufficient_data"]:
        return {"insufficient_data": True, "student": None}
    student = next((s for s in report["students"] if s["user_id"] == user_id), None)
    return {"insufficient_data": student is None, "student": student}
