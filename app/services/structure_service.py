"""structure_service — logique métier Malayka Institution (Structure/Classroom/Invitation).

Phase 1 : création de structure (validation admin), classrooms, invitations
enseignants (nom tapé à l'avance) et leur acceptation.

L'autorisation (qui a le droit d'appeler quoi) est vérifiée dans les routers
via app.services.structure_access — ce module ne fait que la logique métier
une fois l'autorisation déjà confirmée.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.structure import (
    Classroom,
    ClassroomMembership,
    ClassroomRosterEntry,
    ClassroomTeacher,
    InvitationStatus,
    MembershipStatus,
    Structure,
    StructureInvitation,
    StructureInvitationClassroom,
    StructureMember,
    StructureMemberRole,
    StructureStatus,
    StructureType,
)
from app.services.structure_matching import normalize_name

INVITATION_TTL_DAYS = 7


# ── Structure ────────────────────────────────────────────────────────────────

def request_structure_creation(
    db: Session, user_id: uuid.UUID, name: str, country: str | None,
    structure_type: str | None = None, structure_type_other: str | None = None,
    address: str | None = None, email: str | None = None,
) -> Structure:
    """Soumet une demande de création de structure — statut pending.

    Le demandeur devient immédiatement super_admin (peut préparer Classroom/
    invitations pendant la validation), mais la structure ne sera réellement
    opérationnelle (liens d'invitation partageables, etc.) qu'une fois active.
    """
    structure = Structure(
        id=uuid.uuid4(), name=name.strip(), country=country, status=StructureStatus.pending,
        structure_type=StructureType(structure_type) if structure_type else None,
        structure_type_other=structure_type_other.strip() if structure_type_other else None,
        address=address.strip() if address else None,
        email=email.strip() if email else None,
    )
    db.add(structure)
    db.flush()

    member = StructureMember(
        id=uuid.uuid4(), structure_id=structure.id, user_id=user_id,
        role=StructureMemberRole.super_admin,
    )
    db.add(member)
    db.flush()
    return structure


def list_pending_structures(db: Session) -> list[Structure]:
    return list(
        db.execute(
            select(Structure).where(Structure.status == StructureStatus.pending)
        ).scalars().all()
    )


def approve_structure(db: Session, structure_id: uuid.UUID) -> Structure:
    structure = db.get(Structure, structure_id)
    if not structure:
        raise NotFoundError("Structure")
    if structure.status != StructureStatus.pending:
        raise ConflictError("Cette demande a déjà été traitée.")
    structure.status = StructureStatus.active
    db.flush()
    return structure


def reject_structure(db: Session, structure_id: uuid.UUID) -> Structure:
    structure = db.get(Structure, structure_id)
    if not structure:
        raise NotFoundError("Structure")
    if structure.status != StructureStatus.pending:
        raise ConflictError("Cette demande a déjà été traitée.")
    structure.status = StructureStatus.rejected
    db.flush()
    return structure


# ── Classroom ────────────────────────────────────────────────────────────────

def _generate_invite_code(db: Session, length: int = 8) -> str:
    """Code court unique — vérifié contre la base (espace de valeurs plus
    restreint qu'un token d'invitation, donc collision moins négligeable)."""
    for _ in range(10):
        code = secrets.token_hex(length // 2).upper()
        exists = db.execute(
            select(Classroom.id).where(Classroom.invite_code == code)
        ).scalar_one_or_none()
        if not exists:
            return code
    raise RuntimeError("Impossible de générer un invite_code unique.")


def create_classroom(db: Session, structure_id: uuid.UUID, name: str) -> Classroom:
    classroom = Classroom(
        id=uuid.uuid4(), structure_id=structure_id, name=name.strip(),
        invite_code=_generate_invite_code(db),
    )
    db.add(classroom)
    db.flush()
    return classroom


def list_classrooms(db: Session, structure_id: uuid.UUID) -> list[Classroom]:
    return list(
        db.execute(
            select(Classroom).where(Classroom.structure_id == structure_id)
        ).scalars().all()
    )


# ── Invitation enseignant ─────────────────────────────────────────────────────

def create_invitation(
    db: Session,
    *,
    structure_id: uuid.UUID,
    first_name: str,
    last_name: str,
    classroom_ids: list[uuid.UUID],
    contact: str | None,
    created_by_user_id: uuid.UUID,
) -> StructureInvitation:
    # Vérifie que les Classroom visées appartiennent bien à cette structure —
    # évite qu'on invite quelqu'un sur une Classroom d'une autre structure
    # en falsifiant l'ID dans la requête.
    valid_ids = set(
        db.execute(
            select(Classroom.id).where(
                Classroom.id.in_(classroom_ids), Classroom.structure_id == structure_id,
            )
        ).scalars().all()
    )
    if valid_ids != set(classroom_ids):
        raise BadRequestError("Une ou plusieurs Classroom visées sont invalides.")

    invitation = StructureInvitation(
        id=uuid.uuid4(),
        structure_id=structure_id,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        normalized_name=normalize_name(first_name, last_name),
        contact=contact,
        token=secrets.token_urlsafe(32),
        status=InvitationStatus.pending,
        created_by_user_id=created_by_user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITATION_TTL_DAYS),
    )
    db.add(invitation)
    db.flush()

    for classroom_id in classroom_ids:
        db.add(StructureInvitationClassroom(
            id=uuid.uuid4(), invitation_id=invitation.id, classroom_id=classroom_id,
        ))
    db.flush()
    return invitation


def get_invitation_by_token(db: Session, token: str) -> StructureInvitation:
    invitation = db.execute(
        select(StructureInvitation).where(StructureInvitation.token == token)
    ).scalar_one_or_none()
    if not invitation:
        raise NotFoundError("Invitation")
    return invitation


def accept_invitation(
    db: Session, *, token: str, user_id: uuid.UUID, first_name: str, last_name: str,
) -> StructureInvitation:
    """Traite le clic sur le lien d'invitation.

    - Nom saisi correspond au nom tapé par le super_admin → rattachement
      direct : StructureMember(teacher) + ClassroomTeacher pour chaque
      Classroom visée, statut accepted.
    - Ne correspond pas → statut pending_review, en attente de validation
      manuelle par le super_admin (cf. validate_pending_invitation). On
      enregistre quand même qui a cliqué (accepted_user_id) pour que la
      validation manuelle sache à qui accorder l'accès.
    """
    invitation = get_invitation_by_token(db, token)

    if invitation.status != InvitationStatus.pending:
        raise ConflictError("Cette invitation a déjà été traitée ou a expiré.")
    if invitation.expires_at < datetime.now(timezone.utc):
        invitation.status = InvitationStatus.expired
        db.flush()
        raise ConflictError("Cette invitation a expiré.")

    invitation.accepted_user_id = user_id
    entered_name = normalize_name(first_name, last_name)

    if entered_name == invitation.normalized_name:
        _grant_teacher_access(db, invitation, user_id)
        invitation.status = InvitationStatus.accepted
    else:
        invitation.status = InvitationStatus.pending_review

    db.flush()
    return invitation


def validate_pending_invitation(db: Session, invitation_id: uuid.UUID) -> StructureInvitation:
    """Le super_admin approuve manuellement une invitation pending_review
    (le nom saisi par l'invité ne correspondait pas à celui attendu)."""
    invitation = db.get(StructureInvitation, invitation_id)
    if not invitation:
        raise NotFoundError("Invitation")
    if invitation.status != InvitationStatus.pending_review:
        raise ConflictError("Cette invitation n'est pas en attente de validation.")
    if not invitation.accepted_user_id:
        raise ConflictError("Aucun utilisateur n'a encore cliqué sur cette invitation.")

    _grant_teacher_access(db, invitation, invitation.accepted_user_id)
    invitation.status = InvitationStatus.accepted
    db.flush()
    return invitation


def reject_pending_invitation(db: Session, invitation_id: uuid.UUID) -> StructureInvitation:
    invitation = db.get(StructureInvitation, invitation_id)
    if not invitation:
        raise NotFoundError("Invitation")
    if invitation.status != InvitationStatus.pending_review:
        raise ConflictError("Cette invitation n'est pas en attente de validation.")
    invitation.status = InvitationStatus.rejected
    db.flush()
    return invitation


def _grant_teacher_access(
    db: Session, invitation: StructureInvitation, user_id: uuid.UUID,
) -> None:
    """Crée (ou réutilise) le StructureMember(teacher) et les ClassroomTeacher
    pour chaque Classroom visée par l'invitation."""
    member = db.execute(
        select(StructureMember).where(
            StructureMember.structure_id == invitation.structure_id,
            StructureMember.user_id == user_id,
        )
    ).scalar_one_or_none()

    if not member:
        member = StructureMember(
            id=uuid.uuid4(), structure_id=invitation.structure_id, user_id=user_id,
            role=StructureMemberRole.teacher,
        )
        db.add(member)
        db.flush()

    classroom_ids = db.execute(
        select(StructureInvitationClassroom.classroom_id).where(
            StructureInvitationClassroom.invitation_id == invitation.id
        )
    ).scalars().all()

    existing = set(
        db.execute(
            select(ClassroomTeacher.classroom_id).where(
                ClassroomTeacher.structure_member_id == member.id,
                ClassroomTeacher.classroom_id.in_(classroom_ids),
            )
        ).scalars().all()
    )
    for classroom_id in classroom_ids:
        if classroom_id in existing:
            continue
        db.add(ClassroomTeacher(
            id=uuid.uuid4(), classroom_id=classroom_id, structure_member_id=member.id,
        ))
    db.flush()


# ── Roster étudiant (Phase 2) ─────────────────────────────────────────────────

def import_roster(
    db: Session, classroom_id: uuid.UUID, entries: list[tuple[str, str]],
) -> list[ClassroomRosterEntry]:
    """Importe une liste NOM+PRÉNOM — chaque entrée est une place libre que le
    premier étudiant tapant ce nom viendra réclamer."""
    rows = []
    for first_name, last_name in entries:
        row = ClassroomRosterEntry(
            id=uuid.uuid4(), classroom_id=classroom_id,
            first_name=first_name.strip(), last_name=last_name.strip(),
            normalized_name=normalize_name(first_name, last_name),
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def list_roster(db: Session, classroom_id: uuid.UUID) -> list[ClassroomRosterEntry]:
    return list(
        db.execute(
            select(ClassroomRosterEntry)
            .where(ClassroomRosterEntry.classroom_id == classroom_id)
            .order_by(ClassroomRosterEntry.last_name, ClassroomRosterEntry.first_name)
        ).scalars().all()
    )


def get_classroom_by_invite_code(db: Session, invite_code: str) -> Classroom:
    classroom = db.execute(
        select(Classroom).where(Classroom.invite_code == invite_code)
    ).scalar_one_or_none()
    if not classroom:
        raise NotFoundError("Classroom")
    return classroom


def join_classroom(
    db: Session, *, invite_code: str, user_id: uuid.UUID, first_name: str, last_name: str,
) -> ClassroomMembership:
    """Traite le clic sur le lien d'invitation Classroom (étudiant).

    - Nom saisi correspond à une entrée de roster encore libre → rattachement
      direct, l'entrée est marquée réclamée (un autre clic avec le même nom
      n'usurpera plus cette place).
    - Sinon → pending_review, en attente de validation manuelle par
      l'enseignant/super_admin de cette Classroom.
    - Idempotent : un second clic du même utilisateur retourne simplement son
      adhésion existante, sans la retraiter.
    """
    classroom = get_classroom_by_invite_code(db, invite_code)

    existing = db.execute(
        select(ClassroomMembership).where(
            ClassroomMembership.classroom_id == classroom.id,
            ClassroomMembership.user_id == user_id,
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    entered_name = normalize_name(first_name, last_name)
    roster_entry = db.execute(
        select(ClassroomRosterEntry)
        .where(
            ClassroomRosterEntry.classroom_id == classroom.id,
            ClassroomRosterEntry.normalized_name == entered_name,
            ClassroomRosterEntry.claimed_by_user_id.is_(None),
        )
        .order_by(ClassroomRosterEntry.created_at)
    ).scalars().first()

    membership = ClassroomMembership(
        id=uuid.uuid4(), classroom_id=classroom.id, user_id=user_id,
        requested_first_name=first_name.strip(), requested_last_name=last_name.strip(),
        status=MembershipStatus.accepted if roster_entry else MembershipStatus.pending_review,
        roster_entry_id=roster_entry.id if roster_entry else None,
    )
    db.add(membership)
    db.flush()

    if roster_entry:
        roster_entry.claimed_by_user_id = user_id
        roster_entry.claimed_at = datetime.now(timezone.utc)
        db.flush()

    return membership


def list_members(db: Session, classroom_id: uuid.UUID) -> list[ClassroomMembership]:
    return list(
        db.execute(
            select(ClassroomMembership).where(
                ClassroomMembership.classroom_id == classroom_id,
                ClassroomMembership.status == MembershipStatus.accepted,
            )
        ).scalars().all()
    )


def list_join_requests(db: Session, classroom_id: uuid.UUID) -> list[ClassroomMembership]:
    return list(
        db.execute(
            select(ClassroomMembership).where(
                ClassroomMembership.classroom_id == classroom_id,
                ClassroomMembership.status == MembershipStatus.pending_review,
            )
        ).scalars().all()
    )


def validate_join_request(db: Session, membership_id: uuid.UUID) -> ClassroomMembership:
    membership = db.get(ClassroomMembership, membership_id)
    if not membership:
        raise NotFoundError("Demande d'adhésion")
    if membership.status != MembershipStatus.pending_review:
        raise ConflictError("Cette demande n'est pas en attente de validation.")
    membership.status = MembershipStatus.accepted
    db.flush()
    return membership


def reject_join_request(db: Session, membership_id: uuid.UUID) -> ClassroomMembership:
    membership = db.get(ClassroomMembership, membership_id)
    if not membership:
        raise NotFoundError("Demande d'adhésion")
    if membership.status != MembershipStatus.pending_review:
        raise ConflictError("Cette demande n'est pas en attente de validation.")
    membership.status = MembershipStatus.rejected
    db.flush()
    return membership
