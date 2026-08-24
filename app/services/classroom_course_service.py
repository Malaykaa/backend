"""classroom_course_service — cours envoyés par un enseignant à sa Classroom.

Reprend le contrat agent existant (AgentContext/AgentResponse, cf. plan_service.py)
mais persiste dans des tables dédiées (ClassroomCourse*) plutôt que Goal/Plan, pour ne
jamais mélanger contenu envoyé par une structure et activité personnelle de l'étudiant
(cf. structure_access.py).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, AgentResponse
from app.agents.evolution_plan_agent import EvolutionPlanAgent
from app.agents.teacher_course_agent import TeacherCourseAgent
from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.llm import get_llm_provider
from app.models.attachment import Attachment
from app.models.chat import MessageRole
from app.models.notification import UserNotification
from app.models.user import User
from app.models.structure import (
    Classroom,
    ClassroomCourse,
    ClassroomCourseKind,
    ClassroomCourseRecipient,
    ClassroomCourseStep,
    ClassroomCourseStepProgress,
    ClassroomMembership,
    ClassroomStepStatus,
    MembershipStatus,
)
from app.repositories.chat_repo import ChatRepository

logger = logging.getLogger(__name__)


async def create_course(
    db: Session,
    *,
    classroom_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    title: str,
    content: str | None,
    attachment_id: uuid.UUID | None,
    subject: str | None = None,
) -> ClassroomCourse:
    """Analyse le cours soumis (texte et/ou document) et génère son plan d'étapes.

    Appel synchrone à l'agent — même pattern que PlanService.generate_plan : pas de
    file de tâches, juste un appel LLM dans la requête (latence assumée par précédent).
    """
    if not content and not attachment_id:
        raise BadRequestError("Le cours doit contenir du texte ou un document.")

    attachment: Attachment | None = None
    extracted_text: str | None = None
    if attachment_id:
        attachment = db.get(Attachment, attachment_id)
        if not attachment or attachment.pending_user_id != created_by_user_id:
            raise NotFoundError("Document")
        extracted_text = attachment.extracted_text

    message_parts = [f"Titre du cours : {title}"]
    if content:
        message_parts.append(content)
    if extracted_text:
        message_parts.append(f"[Contenu du document joint :\n{extracted_text}]")
    message = "\n\n".join(message_parts)

    ctx = AgentContext(user_id=created_by_user_id, message=message)

    llm = get_llm_provider()
    agent = TeacherCourseAgent(llm)
    try:
        agent_response = await agent.process(ctx)
    except Exception as exc:
        logger.error("Teacher course agent failed for classroom %s: %s", classroom_id, exc)
        agent_response = AgentResponse(
            explanation="Désolé, l'analyse du cours a échoué. Réessaie dans quelques instants.",
            agent_id="error",
        )

    course = ClassroomCourse(
        id=uuid.uuid4(),
        classroom_id=classroom_id,
        created_by_user_id=created_by_user_id,
        title=title.strip(),
        subject=subject.strip() if subject else None,
        kind=ClassroomCourseKind.course,
        raw_content=content,
        attachment_id=attachment.id if attachment else None,
        summary=agent_response.explanation[:300],
        explanation=agent_response.explanation,
        sources=[s.model_dump() for s in agent_response.sources] if agent_response.sources else None,
        suggestions=[s.model_dump() for s in agent_response.suggestions] if agent_response.suggestions else None,
    )
    db.add(course)
    db.flush()

    for step in agent_response.steps:
        db.add(ClassroomCourseStep(
            id=uuid.uuid4(), course_id=course.id,
            label=step.label, description=step.description, order=step.order,
        ))
    db.flush()

    # Le document n'est plus "pending" — il est désormais durablement référencé par
    # ce cours (jamais lié à un message chat, donc pas de link_attachments_to_message).
    if attachment:
        attachment.pending_user_id = None
        attachment.expires_at = None
        db.flush()

    return course


def list_courses(db: Session, classroom_id: uuid.UUID) -> list[ClassroomCourse]:
    return list(
        db.execute(
            select(ClassroomCourse)
            .where(ClassroomCourse.classroom_id == classroom_id)
            .order_by(ClassroomCourse.created_at.desc())
        ).scalars().all()
    )


def get_course(db: Session, course_id: uuid.UUID) -> ClassroomCourse:
    course = db.get(ClassroomCourse, course_id)
    if not course:
        raise NotFoundError("Cours")
    return course


def _notification_url(thread_id: uuid.UUID) -> str:
    return f"/app/chat/{thread_id}"


def _materialize_recipient(db: Session, course: ClassroomCourse, user_id: uuid.UUID) -> ClassroomCourseRecipient:
    """Crée le destinataire effectif d'un cours : thread chat + message + progress
    rows + notification. Appelant responsable de vérifier qu'il n'existe pas déjà
    (cf. send_course/generate_evolution_plans) — pas de vérification d'idempotence ici."""
    settings = get_settings()
    progress_url = f"{settings.frontend_url.rstrip('/')}/classrooms/courses/{course.id}"
    message_content = (
        f"{course.explanation}\n\n"
        f"---\n[Suivre mes étapes pour ce cours →]({progress_url})"
    )

    chat_repo = ChatRepository(db)
    thread = chat_repo.create_thread(user_id=user_id, title=course.title, goal_id=None)
    chat_repo.add_message(
        thread_id=thread.id, role=MessageRole.assistant, content=message_content,
        payload={"agent_id": "teacher_course", "course_id": str(course.id)},
    )

    recipient = ClassroomCourseRecipient(
        id=uuid.uuid4(), course_id=course.id, user_id=user_id, chat_thread_id=thread.id,
    )
    db.add(recipient)
    db.flush()

    for step in course.steps:
        db.add(ClassroomCourseStepProgress(
            id=uuid.uuid4(), recipient_id=recipient.id, step_id=step.id,
            status=ClassroomStepStatus.todo,
        ))

    notif_title = (
        f"Nouveau plan personnalisé : {course.title}" if course.kind == ClassroomCourseKind.evolution_plan
        else f"Nouveau cours : {course.title}"
    )
    notif_url = _notification_url(thread.id)
    db.add(UserNotification(
        user_id=user_id, offer_id=None,
        offer_title=notif_title,
        offer_url=notif_url,
        offer_type="course_assigned",
        score_pct=None, seen=False,
    ))
    from app.services.push_service import send_push
    send_push(db, user_id=user_id, title=notif_title, url=notif_url)

    db.flush()
    return recipient


def send_course(
    db: Session, *, course_id: uuid.UUID, target: str, student_user_id: uuid.UUID | None,
) -> list[ClassroomCourseRecipient]:
    """Matérialise les destinataires d'un cours (toute la Salle ou un seul étudiant).

    Idempotent : un destinataire déjà matérialisé n'est jamais retraité (pas de
    doublon de notification/thread/progression à chaque renvoi).
    """
    course = get_course(db, course_id)

    if target == "classroom":
        recipient_user_ids = set(
            db.execute(
                select(ClassroomMembership.user_id).where(
                    ClassroomMembership.classroom_id == course.classroom_id,
                    ClassroomMembership.status == MembershipStatus.accepted,
                )
            ).scalars().all()
        )
    elif target == "student":
        if not student_user_id:
            raise BadRequestError("student_user_id requis pour un envoi individuel.")
        is_member = db.execute(
            select(ClassroomMembership.id).where(
                ClassroomMembership.classroom_id == course.classroom_id,
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
            select(ClassroomCourseRecipient.user_id).where(
                ClassroomCourseRecipient.course_id == course.id
            )
        ).scalars().all()
    )
    new_user_ids = recipient_user_ids - existing_user_ids

    created = [_materialize_recipient(db, course, user_id) for user_id in new_user_ids]
    return created


def _get_recipient(db: Session, course_id: uuid.UUID, user_id: uuid.UUID) -> ClassroomCourseRecipient:
    recipient = db.execute(
        select(ClassroomCourseRecipient).where(
            ClassroomCourseRecipient.course_id == course_id,
            ClassroomCourseRecipient.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not recipient:
        raise ForbiddenError("Ce cours ne t'a pas été envoyé.")
    return recipient


def get_my_progress(
    db: Session, course_id: uuid.UUID, user_id: uuid.UUID,
) -> tuple[ClassroomCourse, list[ClassroomCourseStepProgress]]:
    course = get_course(db, course_id)
    recipient = _get_recipient(db, course_id, user_id)

    progress = list(
        db.execute(
            select(ClassroomCourseStepProgress)
            .where(ClassroomCourseStepProgress.recipient_id == recipient.id)
        ).scalars().all()
    )
    return course, progress


def complete_step(
    db: Session, *, course_id: uuid.UUID, step_id: uuid.UUID, user_id: uuid.UUID,
) -> ClassroomCourseStepProgress:
    recipient = _get_recipient(db, course_id, user_id)

    progress = db.execute(
        select(ClassroomCourseStepProgress).where(
            ClassroomCourseStepProgress.recipient_id == recipient.id,
            ClassroomCourseStepProgress.step_id == step_id,
        )
    ).scalar_one_or_none()
    if not progress:
        raise NotFoundError("Étape")
    if progress.status == ClassroomStepStatus.done:
        raise ConflictError("Cette étape est déjà terminée.")

    progress.status = ClassroomStepStatus.done
    progress.completed_at = datetime.now(timezone.utc)
    db.flush()
    return progress


def get_progress_matrix(
    db: Session, course_id: uuid.UUID,
) -> list[tuple[ClassroomCourseRecipient, list[ClassroomCourseStepProgress]]]:
    """Pour l'écran enseignant : chaque destinataire avec sa progression par étape."""
    recipients = list(
        db.execute(
            select(ClassroomCourseRecipient).where(ClassroomCourseRecipient.course_id == course_id)
        ).scalars().all()
    )
    result = []
    for recipient in recipients:
        progress = list(
            db.execute(
                select(ClassroomCourseStepProgress)
                .where(ClassroomCourseStepProgress.recipient_id == recipient.id)
            ).scalars().all()
        )
        result.append((recipient, progress))
    return result


# ── Tableaux de bord (Phase 4) ────────────────────────────────────────────────

def step_completion(db: Session, recipient_ids: list[uuid.UUID]) -> tuple[int, int]:
    """Retourne (étapes terminées, étapes totales) pour un ensemble de destinataires."""
    if not recipient_ids:
        return 0, 0
    rows = db.execute(
        select(ClassroomCourseStepProgress.status).where(
            ClassroomCourseStepProgress.recipient_id.in_(recipient_ids)
        )
    ).scalars().all()
    done = sum(1 for s in rows if s == ClassroomStepStatus.done)
    return done, len(rows)


def get_classroom_dashboard(db: Session, classroom_id: uuid.UUID) -> dict:
    """Avancement par cours et par étudiant pour CETTE Classroom uniquement
    (le scope — super_admin ou enseignant assigné — est vérifié par le routeur)."""
    courses = list(
        db.execute(
            select(ClassroomCourse).where(
                ClassroomCourse.classroom_id == classroom_id,
                ClassroomCourse.kind == ClassroomCourseKind.course,
            ).order_by(ClassroomCourse.created_at.desc())
        ).scalars().all()
    )

    course_rows = []
    for course in courses:
        recipient_ids = list(
            db.execute(
                select(ClassroomCourseRecipient.id).where(ClassroomCourseRecipient.course_id == course.id)
            ).scalars().all()
        )
        done, total = step_completion(db, recipient_ids)
        course_rows.append({
            "course_id": course.id, "title": course.title, "subject": course.subject,
            "recipients_count": len(recipient_ids),
            "completion_pct": round(done / total * 100) if total else 0,
        })

    accepted_member_ids = set(
        db.execute(
            select(ClassroomMembership.user_id).where(
                ClassroomMembership.classroom_id == classroom_id,
                ClassroomMembership.status == MembershipStatus.accepted,
            )
        ).scalars().all()
    )

    all_course_ids = list(
        db.execute(
            select(ClassroomCourse.id).where(ClassroomCourse.classroom_id == classroom_id)
        ).scalars().all()
    )

    # Union: membres acceptés + tout destinataire réel d'un cours de cette Classroom
    recipient_member_ids = set(
        db.execute(
            select(ClassroomCourseRecipient.user_id).where(
                ClassroomCourseRecipient.course_id.in_(all_course_ids)
            ).distinct()
        ).scalars().all()
    ) if all_course_ids else set()

    student_user_ids = list(accepted_member_ids | recipient_member_ids)

    student_rows = []
    for user_id in student_user_ids:
        recipient_ids = list(
            db.execute(
                select(ClassroomCourseRecipient.id).where(
                    ClassroomCourseRecipient.user_id == user_id,
                    ClassroomCourseRecipient.course_id.in_(all_course_ids),
                )
            ).scalars().all()
        ) if all_course_ids else []
        done, total = step_completion(db, recipient_ids)
        user = db.get(User, user_id)
        student_rows.append({
            "user_id": user_id, "user_email": user.email if user else None,
            "courses_count": len(recipient_ids), "steps_done": done, "steps_total": total,
            "completion_pct": round(done / total * 100) if total else 0,
        })

    return {"courses": course_rows, "students": student_rows}


def get_structure_dashboard(db: Session, structure_id: uuid.UUID) -> list[dict]:
    """Agrégé sur toutes les Classroom de la structure (super_admin uniquement,
    vérifié par le routeur)."""
    classrooms = list(
        db.execute(select(Classroom).where(Classroom.structure_id == structure_id)).scalars().all()
    )

    rows = []
    for classroom in classrooms:
        students_count = db.execute(
            select(ClassroomMembership.id).where(
                ClassroomMembership.classroom_id == classroom.id,
                ClassroomMembership.status == MembershipStatus.accepted,
            )
        ).scalars().all()
        course_ids = list(
            db.execute(
                select(ClassroomCourse.id).where(
                    ClassroomCourse.classroom_id == classroom.id,
                    ClassroomCourse.kind == ClassroomCourseKind.course,
                )
            ).scalars().all()
        )
        recipient_ids = list(
            db.execute(
                select(ClassroomCourseRecipient.id).where(ClassroomCourseRecipient.course_id.in_(course_ids))
            ).scalars().all()
        ) if course_ids else []
        done, total = step_completion(db, recipient_ids)
        rows.append({
            "classroom_id": classroom.id, "name": classroom.name,
            "students_count": len(students_count), "courses_count": len(course_ids),
            "completion_pct": round(done / total * 100) if total else 0,
        })
    return rows


# ── Génération en lot des plans d'évolution (Phase 4) ────────────────────────

def _build_progress_summary(
    db: Session, *, classroom_id: uuid.UUID, user_id: uuid.UUID, subject: str | None,
) -> str:
    """Synthèse texte de la progression d'un étudiant dans une matière, pour l'agent."""
    courses = list(
        db.execute(
            select(ClassroomCourse).where(
                ClassroomCourse.classroom_id == classroom_id,
                ClassroomCourse.kind == ClassroomCourseKind.course,
                ClassroomCourse.subject == subject,
            )
        ).scalars().all()
    )

    lines = [f"Matière : {subject or 'Général'}"]
    for course in courses:
        recipient = db.execute(
            select(ClassroomCourseRecipient).where(
                ClassroomCourseRecipient.course_id == course.id,
                ClassroomCourseRecipient.user_id == user_id,
            )
        ).scalar_one_or_none()
        if not recipient:
            continue
        progress_by_step = {
            p.step_id: p.status
            for p in db.execute(
                select(ClassroomCourseStepProgress).where(
                    ClassroomCourseStepProgress.recipient_id == recipient.id
                )
            ).scalars().all()
        }
        lines.append(f"\nCours « {course.title} » :")
        for step in course.steps:
            status = progress_by_step.get(step.id, ClassroomStepStatus.todo)
            mark = "[terminé]" if status == ClassroomStepStatus.done else "[non terminé]"
            lines.append(f"- {step.label} {mark}")

    # Résultats réels aux exercices — import local pour éviter un cycle
    # (classroom_exercise_service importe déjà des éléments de ce module).
    from app.services import classroom_exercise_service  # noqa: PLC0415

    detail = classroom_exercise_service.get_student_difficulty_detail(
        db, classroom_id=classroom_id, user_id=user_id, subject=subject,
    )
    student = detail.get("student")
    if student:
        tendance = f", tendance {student['trend']}" if student.get("trend") else ""
        lines.append(
            f"\nRésultats aux exercices — moyenne {student['avg_score_pct']}%{tendance}"
        )
        flagged = student.get("flagged_topics") or []
        if flagged:
            lines.append("Notions les moins maîtrisées (à traiter en priorité) :")
            for f in flagged:
                lines.append(
                    f"- {f['topic_tag']} : {round(f['wrong_rate'] * 100)}% d'erreurs "
                    f"sur {f['questions_seen']} questions"
                )
        else:
            lines.append("Aucune notion en difficulté marquée à ce stade.")

    return "\n".join(lines)


# Namespace du verrou consultatif de génération de plans (cf. le motif de
# app/services/scraping/scheduler.py). Verrou de SESSION — relâché à la
# déconnexion, pas au commit — parce que cette génération committe en cours de
# route. La clé secondaire dérive de l'identifiant de salle : deux salles
# différentes peuvent générer en parallèle, la même salle non.
_EVOLUTION_PLANS_LOCK_NS = 7_002_001


def _classroom_lock_key(classroom_id: uuid.UUID) -> int:
    """Entier signé 32 bits stable dérivé de l'UUID (contrainte de
    pg_try_advisory_lock(int4, int4))."""
    return (classroom_id.int & 0x7FFFFFFF) - 0x40000000


async def generate_evolution_plans(
    db: Session, *, classroom_id: uuid.UUID, created_by_user_id: uuid.UUID,
) -> list[ClassroomCourse]:
    """Génère un plan d'évolution personnalisé par étudiant × matière, à partir
    de la progression réelle de chaque étudiant dans les cours déjà envoyés.
    Auto-envoyé immédiatement — "en une action" inclut la livraison.

    Trois garde-fous, parce que cette fonction enchaîne un appel LLM complet par
    étudiant ET par matière, séquentiellement, dans une seule requête HTTP :

    1. **Verrou par salle** — un double clic de l'enseignant relançait toute la
       génération, et donc toute la facture LLM. Les appels concurrents sur la
       même salle renvoient désormais [] immédiatement.

    2. **Commit par plan** — un échec appelait db.rollback(), ce qui annulait
       AUSSI tous les plans précédents du lot (ils n'étaient que flush()és, le
       commit n'ayant lieu qu'à la toute fin côté routeur). La boucle continuait
       pourtant, et le compteur renvoyé annonçait des plans qui n'existaient
       plus. Chaque plan est maintenant validé indépendamment.

    3. **Reprise idempotente** — les couples (étudiant, matière) déjà pourvus
       d'un plan sont ignorés. Combiné au point 2, cela rend le dépassement de
       délai HTTP (nginx coupe à 120 s, largement atteignable sur une classe
       entière) inoffensif : relancer reprend là où la génération s'est
       arrêtée, sans rien regénérer ni refacturer.

    Reste à faire, hors de cette correction : basculer en tâche de fond avec
    suivi de progression, pour que l'enseignant n'ait plus à relancer du tout.
    """
    lock_args = {"ns": _EVOLUTION_PLANS_LOCK_NS, "k": _classroom_lock_key(classroom_id)}
    if not db.execute(text("SELECT pg_try_advisory_lock(:ns, :k)"), lock_args).scalar():
        logger.info("[Plans IA] génération déjà en cours pour classroom=%s — ignorée", classroom_id)
        return []

    try:
        return await _generate_evolution_plans_locked(
            db, classroom_id=classroom_id, created_by_user_id=created_by_user_id,
        )
    finally:
        # Obligatoire : un verrou de session n'est PAS relâché quand la connexion
        # retourne au pool. Sans cette libération, la première génération
        # bloquerait définitivement toutes les suivantes sur cette salle.
        db.execute(text("SELECT pg_advisory_unlock(:ns, :k)"), lock_args)
        db.commit()


async def _generate_evolution_plans_locked(
    db: Session, *, classroom_id: uuid.UUID, created_by_user_id: uuid.UUID,
) -> list[ClassroomCourse]:
    """Corps de la génération — appelé uniquement avec le verrou de salle tenu."""
    subjects = set(
        db.execute(
            select(ClassroomCourse.subject).where(
                ClassroomCourse.classroom_id == classroom_id,
                ClassroomCourse.kind == ClassroomCourseKind.course,
            )
        ).scalars().all()
    )
    if not subjects:
        return []

    # Utiliser les destinataires réels des cours (pas uniquement les membres acceptés)
    # afin que les plans soient générés pour tous les étudiants ayant reçu au moins un cours.
    course_ids_for_plans = list(
        db.execute(
            select(ClassroomCourse.id).where(
                ClassroomCourse.classroom_id == classroom_id,
                ClassroomCourse.kind == ClassroomCourseKind.course,
            )
        ).scalars().all()
    )
    recipient_user_ids = list(
        set(
            db.execute(
                select(ClassroomCourseRecipient.user_id).where(
                    ClassroomCourseRecipient.course_id.in_(course_ids_for_plans)
                ).distinct()
            ).scalars().all()
        )
    ) if course_ids_for_plans else []

    # Couples (étudiant, matière) déjà pourvus d'un plan : ignorés, pour qu'une
    # relance après dépassement de délai reprenne sans regénérer ni refacturer.
    deja_pourvus: set[tuple[uuid.UUID, str | None]] = set(
        db.execute(
            select(ClassroomCourseRecipient.user_id, ClassroomCourse.subject)
            .join(ClassroomCourse, ClassroomCourseRecipient.course_id == ClassroomCourse.id)
            .where(
                ClassroomCourse.classroom_id == classroom_id,
                ClassroomCourse.kind == ClassroomCourseKind.evolution_plan,
            )
        ).all()
    )

    llm = get_llm_provider()
    agent = EvolutionPlanAgent(llm)
    generated: list[ClassroomCourse] = []

    logger.info(
        "[Plans IA] classroom=%s subjects=%s recipients=%s",
        classroom_id, subjects, recipient_user_ids,
    )

    for user_id in recipient_user_ids:
        for subject in subjects:
            if (user_id, subject) in deja_pourvus:
                logger.info("[Plans IA] skip user=%s subject=%r — plan déjà généré", user_id, subject)
                continue

            summary = _build_progress_summary(db, classroom_id=classroom_id, user_id=user_id, subject=subject)
            logger.info("[Plans IA] user=%s subject=%r summary_len=%d has_progress=%s",
                user_id, subject, len(summary),
                ("[terminé]" in summary or "[non terminé]" in summary),
            )
            if "[terminé]" not in summary and "[non terminé]" not in summary:
                logger.info("[Plans IA] skip user=%s subject=%r — aucun cours reçu", user_id, subject)
                continue

            ctx = AgentContext(user_id=created_by_user_id, message=summary)
            try:
                logger.info("[Plans IA] calling agent for user=%s subject=%r", user_id, subject)
                agent_response = await agent.process(ctx)
                logger.info("[Plans IA] agent OK steps=%d", len(agent_response.steps))
            except Exception as exc:
                logger.error(
                    "[Plans IA] agent FAILED user=%s subject=%r: %s", user_id, subject, exc, exc_info=True,
                )
                continue

            try:
                plan = ClassroomCourse(
                    id=uuid.uuid4(), classroom_id=classroom_id, created_by_user_id=created_by_user_id,
                    title=f"Plan personnalisé — {subject or 'Général'}",
                    subject=subject, kind=ClassroomCourseKind.evolution_plan,
                    summary=agent_response.explanation[:300], explanation=agent_response.explanation,
                    sources=[s.model_dump() for s in agent_response.sources] if agent_response.sources else None,
                    suggestions=[s.model_dump() for s in agent_response.suggestions] if agent_response.suggestions else None,
                )
                db.add(plan)
                db.flush()

                for step in agent_response.steps:
                    db.add(ClassroomCourseStep(
                        id=uuid.uuid4(), course_id=plan.id,
                        label=step.label, description=step.description, order=step.order,
                    ))
                db.flush()

                _materialize_recipient(db, plan, user_id)
                # Validation immédiate : sans elle, un échec ultérieur annulait
                # par rollback tous les plans déjà produits dans ce lot.
                db.commit()
                generated.append(plan)
                logger.info("[Plans IA] plan created + sent to user=%s", user_id)
            except Exception as exc:
                logger.error(
                    "[Plans IA] plan creation FAILED user=%s subject=%r: %s", user_id, subject, exc, exc_info=True,
                )
                # Ne défait que le plan en cours — les précédents sont committés.
                db.rollback()
                continue

    return generated


# ── Plan d'accompagnement declenche par un resultat ──────────────────────────

# Sous ce score, l'evaluation est consideree ratee et un plan d'accompagnement
# est declenche automatiquement. Les objectifs du cours SONT les evaluations :
# rater la moitie des points, c'est ne pas avoir atteint l'objectif.
SUPPORT_PLAN_SCORE_THRESHOLD = 50

# Delai minimal entre deux plans automatiques pour un meme couple
# (eleve, matiere). Sans lui, un eleve qui enchaine plusieurs exercices rates
# recevrait un plan a chaque soumission — spam pour lui, facture LLM pour vous.
SUPPORT_PLAN_COOLDOWN_DAYS = 7


def needs_support_plan(score_pct: int | None) -> bool:
    """Regle unique du declenchement, isolee pour etre testable et modifiable."""
    return score_pct is not None and score_pct < SUPPORT_PLAN_SCORE_THRESHOLD


def _plan_recent_existe(
    db: Session, *, classroom_id: uuid.UUID, user_id: uuid.UUID, subject: str | None,
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=SUPPORT_PLAN_COOLDOWN_DAYS)
    row = db.execute(
        select(ClassroomCourse.id)
        .join(ClassroomCourseRecipient, ClassroomCourseRecipient.course_id == ClassroomCourse.id)
        .where(
            ClassroomCourse.classroom_id == classroom_id,
            ClassroomCourse.kind == ClassroomCourseKind.evolution_plan,
            ClassroomCourse.subject == subject,
            ClassroomCourseRecipient.user_id == user_id,
            ClassroomCourse.created_at >= cutoff,
        )
        .limit(1)
    ).scalar_one_or_none()
    return row is not None


async def generate_support_plan_for_student(
    db: Session, *, classroom_id: uuid.UUID, user_id: uuid.UUID, subject: str | None,
) -> ClassroomCourse | None:
    """Genere et envoie UN plan d'accompagnement, pour un eleve et une matiere.

    Declenche par un resultat insuffisant, non par une action de l'enseignant :
    c'est ce qui fait passer le systeme de "voici ta note" a "voici la suite".

    created_by_user_id reste NULL — cela distingue en base un plan automatique
    d'un plan lance par un enseignant, sans champ supplementaire.

    Retourne None si un plan recent existe deja (cf. cooldown) ou si l'eleve n'a
    recu aucun cours dans cette matiere : sans progression a resumer, l'agent
    n'aurait rien sur quoi s'appuyer.
    """
    if _plan_recent_existe(db, classroom_id=classroom_id, user_id=user_id, subject=subject):
        logger.info(
            "[Plan auto] skip user=%s subject=%r — plan recent deja envoye", user_id, subject,
        )
        return None

    summary = _build_progress_summary(
        db, classroom_id=classroom_id, user_id=user_id, subject=subject,
    )
    if "[terminé]" not in summary and "[non terminé]" not in summary:
        logger.info("[Plan auto] skip user=%s subject=%r — aucun cours recu", user_id, subject)
        return None

    agent = EvolutionPlanAgent(get_llm_provider())
    # user_id de l'eleve, et non de l'enseignant : le contexte agent doit porter
    # celui que le plan concerne.
    response = await agent.process(AgentContext(user_id=user_id, message=summary))

    plan = ClassroomCourse(
        id=uuid.uuid4(), classroom_id=classroom_id, created_by_user_id=None,
        title=f"Plan d'accompagnement — {subject or 'Général'}",
        subject=subject, kind=ClassroomCourseKind.evolution_plan,
        summary=response.explanation[:300], explanation=response.explanation,
        sources=[s.model_dump() for s in response.sources] if response.sources else None,
        suggestions=[s.model_dump() for s in response.suggestions] if response.suggestions else None,
    )
    db.add(plan)
    db.flush()
    for step in response.steps:
        db.add(ClassroomCourseStep(
            id=uuid.uuid4(), course_id=plan.id,
            label=step.label, description=step.description, order=step.order,
        ))
    db.flush()
    _materialize_recipient(db, plan, user_id)
    db.commit()
    logger.info("[Plan auto] plan envoye a user=%s subject=%r", user_id, subject)
    return plan
