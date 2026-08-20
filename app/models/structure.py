"""Malayka Institution — Structure (école/centre), membres, et Classroom (Salle).

Phase 0 : la coquille des données uniquement. Aucune logique de cours/roster
ici (cf. roadmap) — juste ce qu'il faut pour qu'une structure existe, ait des
membres avec un rôle, et possède des classrooms.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class StructureStatus(str, enum.Enum):
    pending  = "pending"   # demande de création soumise, en attente de validation admin
    active   = "active"    # validée, opérationnelle
    rejected = "rejected"  # demande refusée


class StructureType(str, enum.Enum):
    training_center      = "training_center"       # Centre de formation
    independent_trainer  = "independent_trainer"    # Formateur indépendant
    school                = "school"                 # École
    university            = "university"             # Université
    other                 = "other"                  # Autre (préciser)


class StructureMemberRole(str, enum.Enum):
    super_admin = "super_admin"  # voit/gère toutes les Classroom de la structure
    teacher     = "teacher"      # scope limité aux Classroom où il est assigné (ClassroomTeacher)


class InvitationStatus(str, enum.Enum):
    pending         = "pending"          # envoyée, pas encore cliquée
    accepted        = "accepted"         # nom confirmé au clic → rattachement direct
    pending_review  = "pending_review"   # nom saisi ne correspond pas → attend validation du super_admin
    rejected        = "rejected"         # demande en attente refusée par le super_admin
    expired         = "expired"          # non traitée avant expiration


class MembershipStatus(str, enum.Enum):
    accepted       = "accepted"        # nom saisi correspond à une entrée du roster → membre direct
    pending_review = "pending_review"  # ne correspond à aucune entrée libre → attend validation
    rejected       = "rejected"        # demande refusée par l'enseignant/super_admin


class ClassroomStepStatus(str, enum.Enum):
    todo        = "todo"
    in_progress = "in_progress"
    done        = "done"


class ClassroomCourseKind(str, enum.Enum):
    course         = "course"          # soumis par l'enseignant, destiné à plusieurs étudiants
    evolution_plan = "evolution_plan"  # généré par lot, personnalisé pour UN étudiant (Phase 4)


class ClassroomExerciseKind(str, enum.Enum):
    exercise   = "exercise"    # entraînement, tentatives illimitées
    evaluation = "evaluation"  # noté, une seule tentative


class ExerciseSubmissionStatus(str, enum.Enum):
    in_progress = "in_progress"
    # Correction QCM déterministe faite à la soumission — pas d'état "en attente
    # de correction" côté enseignant, contrairement à un devoir à correction humaine.
    submitted = "submitted"


class Structure(Base):
    __tablename__ = "structures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    structure_type: Mapped[StructureType | None] = mapped_column(
        Enum(StructureType, name="structure_type_enum"), nullable=True,
    )
    structure_type_other: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[StructureStatus] = mapped_column(
        Enum(StructureStatus, name="structure_status_enum"),
        nullable=False, default=StructureStatus.pending,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relations
    members: Mapped[list["StructureMember"]] = relationship(
        back_populates="structure", cascade="all, delete-orphan"
    )
    classrooms: Mapped[list["Classroom"]] = relationship(
        back_populates="structure", cascade="all, delete-orphan"
    )


class StructureMember(Base):
    __tablename__ = "structure_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("structures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[StructureMemberRole] = mapped_column(
        Enum(StructureMemberRole, name="structure_member_role_enum"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relations
    structure: Mapped["Structure"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()  # noqa: F821

    __table_args__ = (
        # Un utilisateur n'a qu'un seul rôle par structure (pas de doublon super_admin+teacher).
        UniqueConstraint("structure_id", "user_id", name="uq_structure_member"),
    )


class Classroom(Base):
    __tablename__ = "classrooms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("structures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Code court partagé dans le lien d'invitation étudiant (Phase 2).
    invite_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relation
    structure: Mapped["Structure"] = relationship(back_populates="classrooms")


class ClassroomTeacher(Base):
    """Scope un StructureMember de rôle teacher à une Classroom précise.

    Résout le TODO laissé dans structure_access.get_accessible_classroom_ids
    (Phase 0) : c'est cette table qui permet enfin de déterminer les Classroom
    visibles par un enseignant (par opposition au super_admin, qui voit tout).
    """

    __tablename__ = "classroom_teachers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    classroom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    structure_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("structure_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("classroom_id", "structure_member_id", name="uq_classroom_teacher"),
    )


class StructureInvitation(Base):
    """Invitation nominative à devenir admin (teacher) d'une ou plusieurs Classroom.

    Le super_admin tape à l'avance le nom de la personne invitée (comme un
    roster à une seule entrée). Au clic, le nom saisi par l'invité est comparé
    à celui-ci (normalisé) : correspondance → rattachement direct, sinon →
    pending_review en attente de validation manuelle par le super_admin.
    """

    __tablename__ = "structure_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("structures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(220), nullable=False, index=True)
    contact: Mapped[str | None] = mapped_column(String(320), nullable=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus, name="invitation_status_enum"),
        nullable=False, default=InvitationStatus.pending,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Rempli au clic, que le nom corresponde ou non — on sait toujours qui a
    # cliqué, même si la confirmation finale attend une validation manuelle.
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relations
    structure: Mapped["Structure"] = relationship()
    classrooms: Mapped[list["StructureInvitationClassroom"]] = relationship(
        back_populates="invitation", cascade="all, delete-orphan"
    )


class StructureInvitationClassroom(Base):
    """Classroom(s) visée(s) par une invitation enseignant (many-to-many)."""

    __tablename__ = "structure_invitation_classrooms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invitation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("structure_invitations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    classroom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False, index=True
    )

    invitation: Mapped["StructureInvitation"] = relationship(back_populates="classrooms")
    classroom: Mapped["Classroom"] = relationship()

    __table_args__ = (
        UniqueConstraint("invitation_id", "classroom_id", name="uq_invitation_classroom"),
    )


class ClassroomRosterEntry(Base):
    """Une ligne NOM+PRÉNOM importée par l'enseignant/super_admin pour une Classroom.

    Tant que claimed_by_user_id est NULL, l'entrée est "libre" : un étudiant
    qui clique le lien d'invitation Classroom et tape un nom qui correspond
    (normalisé) à une entrée libre est rattaché directement. Une fois
    réclamée, l'entrée ne peut plus être reprise par un autre clic (évite
    qu'une deuxième personne usurpe la même identité de roster).
    """

    __tablename__ = "classroom_roster_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    classroom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(220), nullable=False, index=True)
    claimed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ClassroomMembership(Base):
    """Rattachement d'un étudiant à une Classroom (issu du clic sur le lien d'invitation).

    requested_first_name/last_name conservent ce que l'étudiant a réellement
    saisi au clic — indépendant de son profil Malayka, qui peut être modifié
    plus tard — pour que l'enseignant puisse juger une demande pending_review
    sur la base de ce qui a été tapé.
    """

    __tablename__ = "classroom_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    classroom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status_enum"),
        nullable=False, default=MembershipStatus.pending_review,
    )
    roster_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_roster_entries.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("classroom_id", "user_id", name="uq_classroom_membership"),
    )


class ClassroomCourse(Base):
    """Cours soumis par un enseignant pour une Classroom (texte libre et/ou document).

    summary/explanation/sources/suggestions reproduisent volontairement la forme de
    Plan (app/models/plan.py) — même contrat de sortie agent, juste une table à part
    pour ne jamais mélanger l'activité personnelle (Goal/Plan) avec du contenu envoyé
    par une structure (cf. structure_access.py).
    """

    __tablename__ = "classroom_courses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    classroom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kind: Mapped[ClassroomCourseKind] = mapped_column(
        Enum(ClassroomCourseKind, name="classroom_course_kind_enum"),
        nullable=False, default=ClassroomCourseKind.course,
    )
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    suggestions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    steps: Mapped[list["ClassroomCourseStep"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="ClassroomCourseStep.order"
    )


class ClassroomCourseStep(Base):
    __tablename__ = "classroom_course_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)

    course: Mapped["ClassroomCourse"] = relationship(back_populates="steps")


class ClassroomCourseRecipient(Base):
    """Un destinataire effectif d'un cours (matérialisé à l'envoi, pas calculé à la volée) —
    permet de suivre la progression même si l'étudiant quitte la Classroom plus tard."""

    __tablename__ = "classroom_course_recipients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_threads.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("course_id", "user_id", name="uq_classroom_course_recipient"),
    )


class ClassroomCourseStepProgress(Base):
    __tablename__ = "classroom_course_step_progress"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_course_recipients.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_course_steps.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status: Mapped[ClassroomStepStatus] = mapped_column(
        Enum(ClassroomStepStatus, name="classroom_step_status_enum"),
        nullable=False, default=ClassroomStepStatus.todo,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("recipient_id", "step_id", name="uq_classroom_step_progress"),
    )


class ClassroomExercise(Base):
    """Exercice ou évaluation QCM créé par un enseignant pour une Classroom.

    Reprend la forme de ClassroomCourse (mêmes conventions classroom→contenu→
    destinataires matérialisés→progression), avec kind pour distinguer un
    entraînement (tentatives illimitées) d'une évaluation notée (une seule
    tentative, cf. classroom_exercise_service.start_submission)."""

    __tablename__ = "classroom_exercises"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    classroom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kind: Mapped[ClassroomExerciseKind] = mapped_column(
        Enum(ClassroomExerciseKind, name="classroom_exercise_kind_enum"),
        nullable=False, default=ClassroomExerciseKind.exercise,
    )
    topic_hint: Mapped[str | None] = mapped_column(String(300), nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_courses.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    questions: Mapped[list["ClassroomExerciseQuestion"]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan", order_by="ClassroomExerciseQuestion.order"
    )


class ClassroomExerciseQuestion(Base):
    __tablename__ = "classroom_exercise_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_exercises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    choices: Mapped[list] = mapped_column(JSON, nullable=False)
    correct_choice_index: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    # Notion couverte par la question (ex. "dérivées composées") — c'est ce tag
    # que classroom_exercise_service.get_classroom_difficulty_report agrège pour
    # regrouper les échecs par notion plutôt que par question isolée.
    topic_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)

    exercise: Mapped["ClassroomExercise"] = relationship(back_populates="questions")


class ClassroomExerciseRecipient(Base):
    """Un destinataire effectif d'un exercice (matérialisé à l'envoi) — miroir
    exact de ClassroomCourseRecipient."""

    __tablename__ = "classroom_exercise_recipients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_exercises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_threads.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("exercise_id", "user_id", name="uq_classroom_exercise_recipient"),
    )


class ClassroomExerciseSubmission(Base):
    """Une tentative d'un élève sur un exercice/évaluation.

    La règle "une seule tentative pour une évaluation" est appliquée dans
    classroom_exercise_service (dépend de exercise.kind, sur une autre table —
    pas modélisable proprement en contrainte SQL ici)."""

    __tablename__ = "classroom_exercise_submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_exercise_recipients.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ExerciseSubmissionStatus] = mapped_column(
        Enum(ExerciseSubmissionStatus, name="exercise_submission_status_enum"),
        nullable=False, default=ExerciseSubmissionStatus.in_progress,
    )
    score_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("recipient_id", "attempt_number", name="uq_classroom_exercise_submission_attempt"),
    )


class ClassroomExerciseAnswer(Base):
    """Réponse d'un élève à une question, dans le cadre d'une soumission.

    Pré-créée (selected_choice_index=NULL) au démarrage de la tentative — même
    logique que ClassroomCourseStepProgress — pour que submit_exercise soit un
    simple pass de mise à jour plutôt qu'un mélange insert/update."""

    __tablename__ = "classroom_exercise_answers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_exercise_submissions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classroom_exercise_questions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    selected_choice_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    points_earned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("submission_id", "question_id", name="uq_classroom_exercise_answer"),
    )
