"""Routes Malayka Institution — Structure, Classroom, invitations enseignants.

Toute vérification "qui a le droit de faire quoi" passe par
app.services.structure_access (point de vérité unique, cf. son docstring).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from pydantic import BaseModel as PydanticBase
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.structure import (
    Classroom,
    ClassroomCourse,
    ClassroomCourseRecipient,
    ClassroomMembership,
    InvitationStatus,
    Structure,
    StructureInvitation,
    StructureInvitationClassroom,
)
from app.models.user import User
from app.schemas.structure import (
    ClassroomCreate,
    ClassroomDashboardResponse,
    ClassroomJoinAccept,
    ClassroomJoinPreview,
    ClassroomJoinResult,
    ClassroomMembershipResponse,
    ClassroomResponse,
    CourseCreate,
    CourseListItem,
    CourseProgressMatrixResponse,
    CourseResponse,
    CourseSendRequest,
    CourseSendResult,
    CourseStepResponse,
    DashboardCourseItem,
    DashboardStudentItem,
    GenerateEvolutionPlansResult,
    ImpactReportClassroomItem,
    ImpactReportResponse,
    ImpactReportSubjectItem,
    InvitationAccept,
    InvitationAcceptResult,
    InvitationCreate,
    InvitationPreview,
    InvitationResponse,
    MyCourseProgressResponse,
    RecipientProgressResponse,
    RosterEntryResponse,
    RosterImportRequest,
    StepProgressResponse,
    StructureCreate,
    StructureDashboardClassroomItem,
    StructureDashboardResponse,
    StructureResponse,
)
from app.llm import get_llm_provider
from app.services import classroom_course_service, impact_report_service, structure_access, structure_service
from app.services.whatsapp_service import whatsapp_service

router = APIRouter(prefix="/structures", tags=["structures"])


def _require_super_admin(db: Session, user_id: uuid.UUID, structure_id: uuid.UUID) -> None:
    if not structure_access.is_super_admin(db, user_id, structure_id):
        raise ForbiddenError("Réservé au super admin de cette structure.")


def _require_member(db: Session, user_id: uuid.UUID, structure_id: uuid.UUID) -> None:
    if structure_access.get_member_role(db, user_id, structure_id) is None:
        raise ForbiddenError("Vous ne faites pas partie de cette structure.")


def _require_classroom_admin(
    db: Session, user_id: uuid.UUID, structure_id: uuid.UUID, classroom_id: uuid.UUID,
) -> Classroom:
    """super_admin de la structure OU enseignant assigné à cette Classroom précise."""
    classroom = db.get(Classroom, classroom_id)
    if not classroom or classroom.structure_id != structure_id:
        raise NotFoundError("Classroom")
    if not structure_access.can_access_classroom(db, user_id, classroom_id):
        raise ForbiddenError("Réservé aux administrateurs de cette Classroom.")
    return classroom


def _classroom_names(db: Session, classroom_ids: list[uuid.UUID]) -> list[str]:
    if not classroom_ids:
        return []
    from app.models.structure import Classroom  # noqa: PLC0415

    return list(
        db.execute(select(Classroom.name).where(Classroom.id.in_(classroom_ids))).scalars().all()
    )


def _invitation_classroom_ids(db: Session, invitation_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(StructureInvitationClassroom.classroom_id).where(
                StructureInvitationClassroom.invitation_id == invitation_id
            )
        ).scalars().all()
    )


# ── Structure ────────────────────────────────────────────────────────────────


@router.post("", response_model=StructureResponse, status_code=201)
def request_structure(
    body: StructureCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    structure = structure_service.request_structure_creation(
        db, current_user.id, body.name, body.country,
        body.structure_type, body.structure_type_other, body.address, body.email,
    )
    db.commit()
    return StructureResponse(
        id=str(structure.id), name=structure.name, country=structure.country,
        structure_type=structure.structure_type.value if structure.structure_type else None,
        structure_type_other=structure.structure_type_other,
        address=structure.address, email=structure.email,
        status=structure.status.value, created_at=structure.created_at, role="super_admin",
    )


@router.get("/me", response_model=list[StructureResponse])
def list_my_structures(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Alimente l'entrée "Structure" du Profil : bascule si non vide, sinon
    proposition de création."""
    structures = structure_access.get_user_structures(db, current_user.id)
    return [
        StructureResponse(
            id=str(s.id), name=s.name, country=s.country,
            structure_type=s.structure_type.value if s.structure_type else None,
            structure_type_other=s.structure_type_other,
            address=s.address, email=s.email,
            status=s.status.value,
            created_at=s.created_at,
            role=structure_access.get_member_role(db, current_user.id, s.id).value,
        )
        for s in structures
    ]


# ── Classroom ────────────────────────────────────────────────────────────────


@router.post("/{structure_id}/classrooms", response_model=ClassroomResponse, status_code=201)
def create_classroom(
    structure_id: uuid.UUID,
    body: ClassroomCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_super_admin(db, current_user.id, structure_id)
    classroom = structure_service.create_classroom(db, structure_id, body.name)
    db.commit()
    return ClassroomResponse(
        id=str(classroom.id), structure_id=str(classroom.structure_id), name=classroom.name,
        invite_code=classroom.invite_code, created_at=classroom.created_at,
    )


@router.get("/{structure_id}/classrooms", response_model=list[ClassroomResponse])
def list_classrooms(
    structure_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    role = structure_access.get_member_role(db, current_user.id, structure_id)
    if role is None:
        raise ForbiddenError("Vous ne faites pas partie de cette structure.")

    classrooms = structure_service.list_classrooms(db, structure_id)
    if role.value == "teacher":
        accessible = structure_access.get_accessible_classroom_ids(db, current_user.id)
        classrooms = [c for c in classrooms if c.id in accessible]

    return [
        ClassroomResponse(
            id=str(c.id), structure_id=str(c.structure_id), name=c.name,
            invite_code=c.invite_code, created_at=c.created_at,
        )
        for c in classrooms
    ]


# ── Roster étudiant & membres (Phase 2) ──────────────────────────────────────


@router.post(
    "/{structure_id}/classrooms/{classroom_id}/roster",
    response_model=list[RosterEntryResponse], status_code=201,
)
def import_roster(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    body: RosterImportRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    entries = structure_service.import_roster(
        db, classroom_id, [(e.first_name, e.last_name) for e in body.entries],
    )
    db.commit()
    return [
        RosterEntryResponse(id=str(r.id), first_name=r.first_name, last_name=r.last_name, claimed=False)
        for r in entries
    ]


@router.get(
    "/{structure_id}/classrooms/{classroom_id}/roster", response_model=list[RosterEntryResponse]
)
def get_roster(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    entries = structure_service.list_roster(db, classroom_id)
    return [
        RosterEntryResponse(
            id=str(r.id), first_name=r.first_name, last_name=r.last_name,
            claimed=r.claimed_by_user_id is not None,
        )
        for r in entries
    ]


def _membership_response(db: Session, m) -> ClassroomMembershipResponse:
    user = db.get(User, m.user_id)
    return ClassroomMembershipResponse(
        id=str(m.id), classroom_id=str(m.classroom_id), user_id=str(m.user_id),
        user_email=user.email if user else None,
        requested_first_name=m.requested_first_name, requested_last_name=m.requested_last_name,
        status=m.status.value, created_at=m.created_at,
    )


@router.get(
    "/{structure_id}/classrooms/{classroom_id}/members", response_model=list[ClassroomMembershipResponse]
)
def list_members(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    members = structure_service.list_members(db, classroom_id)
    return [_membership_response(db, m) for m in members]


@router.get(
    "/{structure_id}/classrooms/{classroom_id}/join-requests",
    response_model=list[ClassroomMembershipResponse],
)
def list_join_requests(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    requests_ = structure_service.list_join_requests(db, classroom_id)
    return [_membership_response(db, m) for m in requests_]


@router.post(
    "/{structure_id}/classrooms/{classroom_id}/join-requests/{membership_id}/validate",
    response_model=ClassroomMembershipResponse,
)
def validate_join_request(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    membership_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    membership = structure_service.validate_join_request(db, membership_id)
    db.commit()
    return _membership_response(db, membership)


@router.post(
    "/{structure_id}/classrooms/{classroom_id}/join-requests/{membership_id}/reject",
    response_model=ClassroomMembershipResponse,
)
def reject_join_request(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    membership_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    membership = structure_service.reject_join_request(db, membership_id)
    db.commit()
    return _membership_response(db, membership)


# ── Lien d'invitation Classroom (public, par invite_code) ───────────────────


@router.get("/classroom-invites/{invite_code}", response_model=ClassroomJoinPreview)
def preview_classroom_invite(invite_code: str, db: Annotated[Session, Depends(get_db)]):
    """Public, non authentifié — affiché avant connexion/inscription."""
    classroom = structure_service.get_classroom_by_invite_code(db, invite_code)
    structure = db.get(Structure, classroom.structure_id)
    return ClassroomJoinPreview(
        structure_name=structure.name if structure else "", classroom_name=classroom.name,
    )


@router.post("/classroom-invites/{invite_code}/join", response_model=ClassroomJoinResult)
def join_classroom(
    invite_code: str,
    body: ClassroomJoinAccept,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    membership = structure_service.join_classroom(
        db, invite_code=invite_code, user_id=current_user.id,
        first_name=body.first_name, last_name=body.last_name,
    )
    db.commit()
    classroom = db.get(Classroom, membership.classroom_id)
    return ClassroomJoinResult(
        status=membership.status.value, classroom_name=classroom.name if classroom else "",
    )


# ── Invitations enseignants ───────────────────────────────────────────────────


@router.post("/{structure_id}/invitations", response_model=InvitationResponse, status_code=201)
def create_invitation(
    structure_id: uuid.UUID,
    body: InvitationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
):
    _require_super_admin(db, current_user.id, structure_id)

    invitation = structure_service.create_invitation(
        db,
        structure_id=structure_id,
        first_name=body.first_name,
        last_name=body.last_name,
        classroom_ids=[uuid.UUID(c) for c in body.classroom_ids],
        contact=body.contact,
        created_by_user_id=current_user.id,
    )
    db.commit()

    settings = get_settings()
    invite_url = f"{settings.frontend_url.rstrip('/')}/structures/invite/{invitation.token}"

    # Envoi WhatsApp best-effort si le contact ressemble à un numéro —
    # aucune infra email n'existe dans ce projet, WhatsApp est le canal
    # établi (cf. OTP). Échec silencieux : le super_admin peut toujours
    # copier/partager invite_url manuellement.
    if body.contact and any(ch.isdigit() for ch in body.contact):
        structure = db.get(Structure, structure_id)
        body_text = (
            f"Vous êtes invité(e) à rejoindre {structure.name if structure else 'une structure'} sur Malayka "
            f"en tant qu'enseignant(e). Cliquez ici pour confirmer : {invite_url}"
        )
        background_tasks.add_task(whatsapp_service.send_message, body.contact, body_text)

    return InvitationResponse(
        id=str(invitation.id), structure_id=str(invitation.structure_id),
        first_name=invitation.first_name, last_name=invitation.last_name,
        status=invitation.status.value, invite_url=invite_url,
        created_at=invitation.created_at, expires_at=invitation.expires_at,
    )


@router.get("/{structure_id}/invitations", response_model=list[InvitationResponse])
def list_invitations(
    structure_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Liste des invitations de la structure — y compris pending_review, pour
    que le super_admin puisse les valider/refuser."""
    _require_super_admin(db, current_user.id, structure_id)
    settings = get_settings()

    invitations = list(
        db.execute(
            select(StructureInvitation).where(StructureInvitation.structure_id == structure_id)
        ).scalars().all()
    )
    return [
        InvitationResponse(
            id=str(inv.id), structure_id=str(inv.structure_id),
            first_name=inv.first_name, last_name=inv.last_name,
            status=inv.status.value,
            invite_url=f"{settings.frontend_url.rstrip('/')}/structures/invite/{inv.token}",
            created_at=inv.created_at, expires_at=inv.expires_at,
        )
        for inv in invitations
    ]


@router.post(
    "/{structure_id}/invitations/{invitation_id}/validate", response_model=InvitationResponse
)
def validate_invitation(
    structure_id: uuid.UUID,
    invitation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_super_admin(db, current_user.id, structure_id)
    invitation = structure_service.validate_pending_invitation(db, invitation_id)
    db.commit()
    settings = get_settings()
    return InvitationResponse(
        id=str(invitation.id), structure_id=str(invitation.structure_id),
        first_name=invitation.first_name, last_name=invitation.last_name,
        status=invitation.status.value,
        invite_url=f"{settings.frontend_url.rstrip('/')}/structures/invite/{invitation.token}",
        created_at=invitation.created_at, expires_at=invitation.expires_at,
    )


@router.post(
    "/{structure_id}/invitations/{invitation_id}/reject", response_model=InvitationResponse
)
def reject_invitation(
    structure_id: uuid.UUID,
    invitation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_super_admin(db, current_user.id, structure_id)
    invitation = structure_service.reject_pending_invitation(db, invitation_id)
    db.commit()
    settings = get_settings()
    return InvitationResponse(
        id=str(invitation.id), structure_id=str(invitation.structure_id),
        first_name=invitation.first_name, last_name=invitation.last_name,
        status=invitation.status.value,
        invite_url=f"{settings.frontend_url.rstrip('/')}/structures/invite/{invitation.token}",
        created_at=invitation.created_at, expires_at=invitation.expires_at,
    )


# ── Acceptation (lien public puis confirmation authentifiée) ────────────────


@router.get("/invitations/{token}", response_model=InvitationPreview)
def preview_invitation(token: str, db: Annotated[Session, Depends(get_db)]):
    """Public, non authentifié — affiché avant connexion/inscription."""
    invitation = structure_service.get_invitation_by_token(db, token)
    structure = db.get(Structure, invitation.structure_id)
    classroom_ids = _invitation_classroom_ids(db, invitation.id)
    return InvitationPreview(
        structure_name=structure.name if structure else "",
        classroom_names=_classroom_names(db, classroom_ids),
        first_name=invitation.first_name, last_name=invitation.last_name,
        status=invitation.status.value,
    )


@router.post("/invitations/{token}/accept", response_model=InvitationAcceptResult)
def accept_invitation(
    token: str,
    body: InvitationAccept,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    invitation = structure_service.accept_invitation(
        db, token=token, user_id=current_user.id,
        first_name=body.first_name, last_name=body.last_name,
    )
    db.commit()
    structure = db.get(Structure, invitation.structure_id)
    classroom_ids = _invitation_classroom_ids(db, invitation.id)
    return InvitationAcceptResult(
        status=invitation.status.value,
        structure_name=structure.name if structure else "",
        classroom_names=_classroom_names(db, classroom_ids),
    )


# ── Cours (Phase 3) ───────────────────────────────────────────────────────────


def _course_response(course: ClassroomCourse) -> CourseResponse:
    return CourseResponse(
        id=str(course.id), classroom_id=str(course.classroom_id), title=course.title,
        subject=course.subject, kind=course.kind.value,
        summary=course.summary, explanation=course.explanation, suggestions=course.suggestions,
        created_at=course.created_at,
        steps=[
            CourseStepResponse(id=str(s.id), label=s.label, description=s.description, order=s.order)
            for s in course.steps
        ],
    )


@router.post(
    "/{structure_id}/classrooms/{classroom_id}/courses", response_model=CourseResponse, status_code=201,
)
async def create_course(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    body: CourseCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    course = await classroom_course_service.create_course(
        db, classroom_id=classroom_id, created_by_user_id=current_user.id,
        title=body.title, content=body.content,
        attachment_id=uuid.UUID(body.attachment_id) if body.attachment_id else None,
        subject=body.subject,
    )
    db.commit()
    return _course_response(course)


@router.get("/{structure_id}/classrooms/{classroom_id}/courses", response_model=list[CourseListItem])
def list_courses(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    courses = classroom_course_service.list_courses(db, classroom_id)

    items = []
    for c in courses:
        recipients_count = db.execute(
            select(ClassroomCourseRecipient).where(ClassroomCourseRecipient.course_id == c.id)
        ).scalars().all()
        items.append(CourseListItem(
            id=str(c.id), classroom_id=str(c.classroom_id), title=c.title,
            subject=c.subject, kind=c.kind.value, summary=c.summary,
            created_at=c.created_at, steps_count=len(c.steps), recipients_count=len(recipients_count),
        ))
    return items


@router.get(
    "/{structure_id}/classrooms/{classroom_id}/courses/{course_id}", response_model=CourseResponse,
)
def get_course(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    course_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    course = classroom_course_service.get_course(db, course_id)
    return _course_response(course)


@router.get(
    "/{structure_id}/classrooms/{classroom_id}/courses/{course_id}/progress",
    response_model=CourseProgressMatrixResponse,
)
def get_course_progress(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    course_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    course = classroom_course_service.get_course(db, course_id)
    matrix = classroom_course_service.get_progress_matrix(db, course_id)

    steps_by_id = {s.id: s for s in course.steps}
    recipients = []
    for recipient, progress_rows in matrix:
        user = db.get(User, recipient.user_id)
        membership = db.execute(
            select(ClassroomMembership).where(
                ClassroomMembership.user_id == recipient.user_id,
                ClassroomMembership.classroom_id == classroom_id,
            )
        ).scalar_one_or_none()
        if membership:
            user_name = f"{membership.requested_first_name} {membership.requested_last_name}".strip()
        elif user and user.profile:
            parts = [user.profile.first_name, user.profile.last_name]
            user_name = " ".join(p for p in parts if p) or None
        else:
            user_name = None
        recipients.append(RecipientProgressResponse(
            user_id=str(recipient.user_id),
            user_email=user.email if user else None,
            user_name=user_name or None,
            user_phone=user.phone if user else None,
            steps=[
                StepProgressResponse(
                    step_id=str(p.step_id), label=steps_by_id[p.step_id].label,
                    description=steps_by_id[p.step_id].description, order=steps_by_id[p.step_id].order,
                    status=p.status.value, completed_at=p.completed_at,
                )
                for p in sorted(progress_rows, key=lambda p: steps_by_id[p.step_id].order)
                if p.step_id in steps_by_id
            ],
        ))

    return CourseProgressMatrixResponse(
        course_id=str(course.id), title=course.title, recipients=recipients,
    )


@router.post(
    "/{structure_id}/classrooms/{classroom_id}/courses/{course_id}/send",
    response_model=CourseSendResult,
)
def send_course(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    course_id: uuid.UUID,
    body: CourseSendRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    new_recipients = classroom_course_service.send_course(
        db, course_id=course_id, target=body.target,
        student_user_id=uuid.UUID(body.student_user_id) if body.student_user_id else None,
    )
    db.commit()
    return CourseSendResult(new_recipients_count=len(new_recipients))


@router.get("/classroom-courses/{course_id}/my-progress", response_model=MyCourseProgressResponse)
def get_my_course_progress(
    course_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    course, progress = classroom_course_service.get_my_progress(db, course_id, current_user.id)
    steps_by_id = {s.id: s for s in course.steps}
    return MyCourseProgressResponse(
        course_id=str(course.id), title=course.title, explanation=course.explanation,
        steps=[
            StepProgressResponse(
                step_id=str(p.step_id), label=steps_by_id[p.step_id].label,
                description=steps_by_id[p.step_id].description, order=steps_by_id[p.step_id].order,
                status=p.status.value, completed_at=p.completed_at,
            )
            for p in sorted(progress, key=lambda p: steps_by_id[p.step_id].order)
            if p.step_id in steps_by_id
        ],
    )


@router.post(
    "/classroom-courses/{course_id}/steps/{step_id}/complete", response_model=StepProgressResponse,
)
def complete_course_step(
    course_id: uuid.UUID,
    step_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    progress = classroom_course_service.complete_step(
        db, course_id=course_id, step_id=step_id, user_id=current_user.id,
    )
    db.commit()
    course = classroom_course_service.get_course(db, course_id)
    step = next(s for s in course.steps if s.id == step_id)
    return StepProgressResponse(
        step_id=str(step.id), label=step.label, description=step.description, order=step.order,
        status=progress.status.value, completed_at=progress.completed_at,
    )


# ── Tableaux de bord & génération en lot (Phase 4) ───────────────────────────


@router.get(
    "/{structure_id}/classrooms/{classroom_id}/dashboard", response_model=ClassroomDashboardResponse,
)
def get_classroom_dashboard(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Avancement par cours et par étudiant — scope cette Classroom uniquement
    (super_admin ou enseignant assigné, jamais plus large)."""
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    dashboard = classroom_course_service.get_classroom_dashboard(db, classroom_id)
    return ClassroomDashboardResponse(
        courses=[
            DashboardCourseItem(
                course_id=str(c["course_id"]), title=c["title"], subject=c["subject"],
                recipients_count=c["recipients_count"], completion_pct=c["completion_pct"],
            )
            for c in dashboard["courses"]
        ],
        students=[
            DashboardStudentItem(
                user_id=str(s["user_id"]), user_email=s["user_email"],
                courses_count=s["courses_count"], steps_done=s["steps_done"],
                steps_total=s["steps_total"], completion_pct=s["completion_pct"],
            )
            for s in dashboard["students"]
        ],
    )


@router.get("/{structure_id}/dashboard", response_model=StructureDashboardResponse)
def get_structure_dashboard(
    structure_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Agrégé sur toutes les Classroom de la structure — réservé au super_admin."""
    _require_super_admin(db, current_user.id, structure_id)
    rows = classroom_course_service.get_structure_dashboard(db, structure_id)
    return StructureDashboardResponse(
        classrooms=[
            StructureDashboardClassroomItem(
                classroom_id=str(r["classroom_id"]), name=r["name"],
                students_count=r["students_count"], courses_count=r["courses_count"],
                completion_pct=r["completion_pct"],
            )
            for r in rows
        ],
    )


@router.post(
    "/{structure_id}/classrooms/{classroom_id}/evolution-plans",
    response_model=GenerateEvolutionPlansResult,
)
async def generate_evolution_plans(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Génère et envoie, en une action, un plan d'évolution personnalisé par
    étudiant × matière, basé sur la progression réelle de chaque étudiant."""
    _require_classroom_admin(db, current_user.id, structure_id, classroom_id)
    plans = await classroom_course_service.generate_evolution_plans(
        db, classroom_id=classroom_id, created_by_user_id=current_user.id,
    )
    db.commit()
    return GenerateEvolutionPlansResult(plans_generated=len(plans))


# ── Rapports d'impact pour bailleurs/accréditations (Phase 5) ────────────────


def _impact_report_response(report: dict) -> ImpactReportResponse:
    return ImpactReportResponse(
        structure_id=str(report["structure_id"]), structure_name=report["structure_name"],
        generated_at=report["generated_at"], period_since=report["period_since"], period_until=report["period_until"],
        classrooms_count=report["classrooms_count"], teachers_count=report["teachers_count"],
        students_count=report["students_count"], courses_count=report["courses_count"],
        evolution_plans_count=report["evolution_plans_count"], completion_pct=report["completion_pct"],
        by_classroom=[
            ImpactReportClassroomItem(
                classroom_id=str(c["classroom_id"]), name=c["name"], students_count=c["students_count"],
                courses_count=c["courses_count"], completion_pct=c["completion_pct"],
            )
            for c in report["by_classroom"]
        ],
        by_subject=[
            ImpactReportSubjectItem(
                subject=s["subject"], courses_count=s["courses_count"], completion_pct=s["completion_pct"],
            )
            for s in report["by_subject"]
        ],
    )


@router.get("/{structure_id}/impact-report", response_model=ImpactReportResponse)
def get_impact_report(
    structure_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    since: datetime | None = None,
    until: datetime | None = None,
):
    """Rapport d'impact agrégé sur toute la structure — réservé au super_admin,
    destiné aux exports bailleurs/accréditations."""
    _require_super_admin(db, current_user.id, structure_id)
    report = impact_report_service.build_impact_report(db, structure_id=structure_id, since=since, until=until)
    return _impact_report_response(report)


@router.get("/{structure_id}/impact-report/export")
def export_impact_report(
    structure_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    format: Literal["pdf", "csv"] = "pdf",
    since: datetime | None = None,
    until: datetime | None = None,
):
    """Export téléchargeable (PDF ou CSV) du rapport d'impact — réservé au super_admin."""
    _require_super_admin(db, current_user.id, structure_id)
    report = impact_report_service.build_impact_report(db, structure_id=structure_id, since=since, until=until)

    safe_name = report["structure_name"].lower().replace(" ", "_")
    if format == "csv":
        content = impact_report_service.export_csv(report)
        return Response(
            content=content, media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="rapport_impact_{safe_name}.csv"'},
        )

    pdf_bytes = impact_report_service.export_pdf(report)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="rapport_impact_{safe_name}.pdf"'},
    )


# ── Assistance IA par section (éditeur de cours) ─────────────────────────────


class _AiAssistRequest(PydanticBase):
    section_content: str
    action: Literal["analyze", "develop", "correct"]


class _AiAssistResponse(PydanticBase):
    result: str


_AI_ASSIST_PROMPTS: dict[str, str] = {
    "analyze": (
        "Tu es un expert pédagogique. Analyse ce contenu de cours et fournis un résumé clair "
        "des concepts clés, de la structure logique et des points importants. "
        "Réponds en Markdown structuré, en français.\n\nContenu :\n{content}"
    ),
    "develop": (
        "Tu es un expert pédagogique. Développe et enrichis ce contenu de cours : "
        "ajoute des explications, des exemples concrets, des définitions et des clarifications utiles. "
        "Conserve le style et le niveau du contenu original. "
        "Réponds en Markdown structuré, en français.\n\nContenu :\n{content}"
    ),
    "correct": (
        "Tu es un expert pédagogique. Corrige, améliore et restructure ce contenu de cours : "
        "corrige les fautes d'orthographe et de grammaire, améliore la clarté et la fluidité, "
        "organise les idées de manière logique et pédagogique. "
        "Conserve le sens et les informations essentielles. "
        "Réponds en Markdown structuré, en français.\n\nContenu :\n{content}"
    ),
}


@router.post(
    "/{structure_id}/classrooms/{classroom_id}/ai-assist",
    response_model=_AiAssistResponse,
)
async def ai_assist_section(
    structure_id: uuid.UUID,
    classroom_id: uuid.UUID,
    body: _AiAssistRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Assistance IA ponctuelle sur une section de cours (analyser / développer / corriger)."""
    _require_member(db, current_user.id, structure_id)
    if not body.section_content.strip():
        return _AiAssistResponse(result="")
    prompt = _AI_ASSIST_PROMPTS[body.action].format(content=body.section_content[:8000])
    llm = get_llm_provider()
    result = await llm.complete(
        [
            {"role": "system", "content": "Tu es un assistant pédagogique expert. Réponds toujours en français."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=2000,
    )
    return _AiAssistResponse(result=result)
