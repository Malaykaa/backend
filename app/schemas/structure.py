"""Schemas Pydantic — Malayka Institution (Structure / Classroom / Invitation)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

StructureTypeLiteral = Literal[
    "training_center", "independent_trainer", "school", "university", "other"
]


class StructureCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    country: str | None = None
    structure_type: StructureTypeLiteral | None = None
    structure_type_other: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    email: EmailStr | None = None

    @model_validator(mode="after")
    def _other_requires_label(self) -> "StructureCreate":
        if self.structure_type == "other" and not (self.structure_type_other or "").strip():
            raise ValueError("Précise le type de structure.")
        return self


class StructureResponse(BaseModel):
    id: str
    name: str
    country: str | None
    structure_type: str | None = None
    structure_type_other: str | None = None
    address: str | None = None
    email: str | None = None
    status: str
    created_at: datetime
    role: str  # rôle du current_user dans cette structure (super_admin | teacher)


class ClassroomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class ClassroomResponse(BaseModel):
    id: str
    structure_id: str
    name: str
    invite_code: str
    created_at: datetime


class ArchiveResult(BaseModel):
    """Resultat d'un archivage/desarchivage. `archived` reflete l'etat APRES
    l'operation, pour que l'appelant n'ait pas a le deduire."""

    id: str
    archived: bool


class RemoveResult(BaseModel):
    """Resultat d'un retrait/retablissement de membre. `removed` reflete l'etat
    APRES l'operation."""

    user_id: str
    removed: bool


class InvitationCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    classroom_ids: list[str] = Field(min_length=1)
    contact: str | None = Field(default=None, max_length=320)


class InvitationResponse(BaseModel):
    id: str
    structure_id: str
    first_name: str
    last_name: str
    status: str
    invite_url: str
    created_at: datetime
    expires_at: datetime


class InvitationPreview(BaseModel):
    """Aperçu public (non authentifié) — affiché avant connexion/inscription."""

    structure_name: str
    classroom_names: list[str]
    first_name: str
    last_name: str
    status: str


class InvitationAccept(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)


class InvitationAcceptResult(BaseModel):
    status: str  # "accepted" | "pending_review"
    structure_name: str
    classroom_names: list[str]


class RosterEntryCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)


class RosterImportRequest(BaseModel):
    entries: list[RosterEntryCreate] = Field(min_length=1)


class RosterEntryResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    claimed: bool


class ClassroomMembershipResponse(BaseModel):
    id: str
    classroom_id: str
    user_id: str
    user_email: str | None
    requested_first_name: str
    requested_last_name: str
    status: str
    created_at: datetime


class ClassroomJoinPreview(BaseModel):
    structure_name: str
    classroom_name: str


class ClassroomJoinAccept(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)


class ClassroomJoinResult(BaseModel):
    status: str  # "accepted" | "pending_review"
    classroom_name: str


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str | None = None
    attachment_id: str | None = None
    subject: str | None = Field(default=None, max_length=100)


class CourseStepResponse(BaseModel):
    id: str
    label: str
    description: str
    order: int


class CourseResponse(BaseModel):
    id: str
    classroom_id: str
    title: str
    subject: str | None
    kind: str
    summary: str
    explanation: str
    suggestions: list[dict] | None
    created_at: datetime
    steps: list[CourseStepResponse]


class CourseListItem(BaseModel):
    id: str
    classroom_id: str
    title: str
    subject: str | None
    kind: str
    summary: str
    created_at: datetime
    steps_count: int
    recipients_count: int


class CourseSendRequest(BaseModel):
    target: str  # "classroom" | "student"
    student_user_id: str | None = None


class CourseSendResult(BaseModel):
    new_recipients_count: int


class StepProgressResponse(BaseModel):
    step_id: str
    label: str
    description: str
    order: int
    status: str
    completed_at: datetime | None


class MyCourseProgressResponse(BaseModel):
    course_id: str
    title: str
    explanation: str
    steps: list[StepProgressResponse]


class RecipientProgressResponse(BaseModel):
    user_id: str
    user_email: str | None
    user_name: str | None
    user_phone: str | None
    steps: list[StepProgressResponse]


class CourseProgressMatrixResponse(BaseModel):
    course_id: str
    title: str
    recipients: list[RecipientProgressResponse]


class DashboardCourseItem(BaseModel):
    course_id: str
    title: str
    subject: str | None
    recipients_count: int
    completion_pct: int


class DashboardStudentItem(BaseModel):
    user_id: str
    user_email: str | None
    courses_count: int
    steps_done: int
    steps_total: int
    completion_pct: int


class ClassroomDashboardResponse(BaseModel):
    courses: list[DashboardCourseItem]
    students: list[DashboardStudentItem]


class StructureDashboardClassroomItem(BaseModel):
    classroom_id: str
    name: str
    students_count: int
    courses_count: int
    completion_pct: int


class StructureDashboardResponse(BaseModel):
    classrooms: list[StructureDashboardClassroomItem]


class GenerateEvolutionPlansResult(BaseModel):
    plans_generated: int


class ImpactReportClassroomItem(BaseModel):
    classroom_id: str
    name: str
    students_count: int
    courses_count: int
    completion_pct: int


class ImpactReportSubjectItem(BaseModel):
    subject: str
    courses_count: int
    completion_pct: int


class ImpactReportResponse(BaseModel):
    structure_id: str
    structure_name: str
    generated_at: datetime
    period_since: datetime | None
    period_until: datetime | None
    classrooms_count: int
    teachers_count: int
    students_count: int
    courses_count: int
    evolution_plans_count: int
    completion_pct: int
    by_classroom: list[ImpactReportClassroomItem]
    by_subject: list[ImpactReportSubjectItem]


# ── Exercices / évaluations (QCM) ──────────────────────────────────────────


class ExerciseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    topic_hint: str = Field(min_length=1, max_length=300)
    subject: str | None = Field(default=None, max_length=100)
    kind: str = "exercise"  # "exercise" | "evaluation"
    question_count: int = Field(default=8, ge=1, le=20)
    source_course_id: str | None = None


class QuestionEditInput(BaseModel):
    prompt: str = Field(min_length=1)
    choices: list[str] = Field(min_length=2, max_length=6)
    correct_choice_index: int
    explanation: str | None = None
    topic_tag: str | None = None
    points: int = Field(default=1, ge=1)


class ExerciseQuestionsUpdate(BaseModel):
    questions: list[QuestionEditInput] = Field(min_length=1)


class ExerciseQuestionAnswerKeyResponse(BaseModel):
    """Vue enseignant — inclut la bonne réponse."""

    id: str
    prompt: str
    choices: list[str]
    correct_choice_index: int
    explanation: str | None
    points: int
    order: int
    topic_tag: str | None


class ExerciseResponse(BaseModel):
    id: str
    classroom_id: str
    title: str
    subject: str | None
    kind: str
    topic_hint: str | None
    instructions: str | None
    source_course_id: str | None
    created_at: datetime
    questions: list[ExerciseQuestionAnswerKeyResponse]


class ExerciseListItem(BaseModel):
    id: str
    classroom_id: str
    title: str
    subject: str | None
    kind: str
    created_at: datetime
    questions_count: int
    recipients_count: int


class ExerciseSendRequest(BaseModel):
    target: str  # "classroom" | "student"
    student_user_id: str | None = None


class ExerciseSendResult(BaseModel):
    new_recipients_count: int


class ExerciseTakeQuestion(BaseModel):
    """Vue élève — JAMAIS correct_choice_index ni explanation avant soumission."""

    id: str
    prompt: str
    choices: list[str]
    order: int


class ExerciseTakeResponse(BaseModel):
    exercise_id: str
    title: str
    instructions: str | None
    kind: str
    questions: list[ExerciseTakeQuestion]


class ExerciseSubmissionResponse(BaseModel):
    submission_id: str
    attempt_number: int
    status: str


class ExerciseAnswerInput(BaseModel):
    question_id: str
    selected_choice_index: int | None = None


class ExerciseSubmitRequest(BaseModel):
    answers: list[ExerciseAnswerInput]


class ExerciseAnswerResult(BaseModel):
    question_id: str
    prompt: str
    choices: list[str]
    selected_choice_index: int | None
    correct_choice_index: int
    is_correct: bool
    explanation: str | None


class ExerciseResultResponse(BaseModel):
    exercise_id: str
    attempt_number: int
    status: str
    score_points: int | None
    max_points: int | None
    score_pct: int | None
    submitted_at: datetime | None
    answers: list[ExerciseAnswerResult]


class ExerciseAttemptSummary(BaseModel):
    attempt_number: int
    score_pct: int | None
    submitted_at: datetime | None


class ExerciseRecipientResult(BaseModel):
    user_id: str
    user_email: str | None
    user_name: str | None
    attempted: bool
    score_pct: int | None
    submitted_at: datetime | None


class ExerciseResultsMatrixResponse(BaseModel):
    exercise_id: str
    title: str
    kind: str
    recipients: list[ExerciseRecipientResult]


class StudentTopicFlag(BaseModel):
    topic_tag: str
    wrong_rate: float
    questions_seen: int


class ClassroomDifficultyStudentItem(BaseModel):
    user_id: str
    user_name: str | None
    avg_score_pct: int
    flagged_topics: list[StudentTopicFlag]
    trend: str | None  # "improving" | "flat" | "declining" | None


class StudentMasteredTopic(BaseModel):
    """Notion demontree en evaluation. Le seuil de maitrise est plus exigeant que
    celui de difficulte : annoncer une competence acquise engage davantage qu'un
    signalement de difficulte."""

    topic_tag: str
    success_rate: float
    questions_seen: int


class MyNextStepsItem(BaseModel):
    """Ce que l'eleve a valide dans une salle, et ce que cela lui ouvre.

    Pendant de MyDifficultyItem : jusqu'ici un eleve qui reussissait ne recevait
    rien. Les opportunites viennent de scraped_offers, jamais d'une generation.
    """

    classroom_id: str
    classroom_name: str
    mastered_topics: list[StudentMasteredTopic]
    opportunities: list[StudentResourceItem] = []


class StudentResourceItem(BaseModel):
    """Ressource proposee a un eleve en difficulte sur une notion.

    Meme forme que les cartes d'offre du chat : contenu issu de la base,
    jamais d'une generation.
    """

    offer_ref: str
    title: str
    url: str | None = None
    company: str | None = None
    type: str | None = None
    description: str | None = None


class MyDifficultyItem(BaseModel):
    """Vue ELEVE de son propre diagnostic, par salle.

    Volontairement sans le champ `topics` du rapport de classe, qui agrege les
    resultats des autres eleves : cette reponse ne doit contenir que ce qui
    concerne celui qui la demande.
    """

    classroom_id: str
    classroom_name: str
    avg_score_pct: int
    trend: str | None  # "improving" | "flat" | "declining" | None
    flagged_topics: list[StudentTopicFlag]
    # Ressources reelles de la base correspondant aux notions ratees. Jamais
    # generees : lignes de scraped_offers servies telles quelles.
    resources: list[StudentResourceItem] = []


class TopicDifficultyItem(BaseModel):
    topic_tag: str
    class_success_rate: int
    students_flagged_count: int


class ClassroomDifficultyReportResponse(BaseModel):
    students: list[ClassroomDifficultyStudentItem]
    topics: list[TopicDifficultyItem]
    insufficient_data: bool


class StudentDifficultyDetailResponse(BaseModel):
    insufficient_data: bool
    student: ClassroomDifficultyStudentItem | None


# ── Index élève : cours + exercices reçus, tous confondus ─────────────────


class DeliveryItem(BaseModel):
    kind: str  # "course" | "evolution_plan" | "exercise" | "evaluation"
    id: str
    title: str
    classroom_name: str
    created_at: datetime
    completion_pct: int
    score_pct: int | None


class MyDeliveriesResponse(BaseModel):
    items: list[DeliveryItem]
