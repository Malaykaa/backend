"""structure_access — point de vérité unique pour la confidentialité Malayka Institution.

Toute décision "qu'est-ce que cet utilisateur a le droit de voir/gérer" dans le
contexte structure/classroom doit passer par ce module — jamais ré-implémentée
ailleurs. Ce module ne lit QUE Structure/StructureMember/Classroom : il ne
touche jamais Goal/Plan/Document (l'activité personnelle de l'utilisateur),
qui doivent rester invisibles à toute structure, sans exception.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.structure import (
    Classroom,
    ClassroomTeacher,
    Structure,
    StructureMember,
    StructureMemberRole,
)


def get_member_role(
    db: Session, user_id: uuid.UUID, structure_id: uuid.UUID
) -> StructureMemberRole | None:
    """Rôle de cet utilisateur dans cette structure, None s'il n'en fait pas partie."""
    return db.execute(
        select(StructureMember.role).where(
            StructureMember.user_id == user_id,
            StructureMember.structure_id == structure_id,
        )
    ).scalar_one_or_none()


def is_super_admin(db: Session, user_id: uuid.UUID, structure_id: uuid.UUID) -> bool:
    return get_member_role(db, user_id, structure_id) == StructureMemberRole.super_admin


def get_user_structures(db: Session, user_id: uuid.UUID) -> list[Structure]:
    """Structures où cet utilisateur a un rôle (super_admin ou teacher).

    Alimente l'entrée "Structure" du Profil : bascule si non vide, proposition
    de création si vide.
    """
    return list(
        db.execute(
            select(Structure)
            .join(StructureMember, StructureMember.structure_id == Structure.id)
            .where(StructureMember.user_id == user_id)
        ).scalars().all()
    )


def get_accessible_classroom_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Toutes les Classroom que cet utilisateur a le droit de consulter/gérer.

    - super_admin d'une structure → toutes les Classroom de cette structure.
    - teacher → uniquement ses Classroom assignées (ClassroomTeacher) et non retirées.

    Les salles ARCHIVÉES restent volontairement accessibles : sans cela, les
    désarchiver deviendrait impossible et l'archivage redeviendrait irréversible
    — exactement le défaut qu'il corrige. Ce sont les listes (list_classrooms)
    qui les masquent par défaut, pas le contrôle d'accès.
    """
    super_admin_structure_ids = set(
        db.execute(
            select(StructureMember.structure_id).where(
                StructureMember.user_id == user_id,
                StructureMember.role == StructureMemberRole.super_admin,
            )
        ).scalars().all()
    )

    classroom_ids: set[uuid.UUID] = set()
    if super_admin_structure_ids:
        classroom_ids.update(
            db.execute(
                select(Classroom.id).where(Classroom.structure_id.in_(super_admin_structure_ids))
            ).scalars().all()
        )

    classroom_ids.update(
        db.execute(
            select(ClassroomTeacher.classroom_id)
            .join(StructureMember, StructureMember.id == ClassroomTeacher.structure_member_id)
            .where(
                StructureMember.user_id == user_id,
                StructureMember.role == StructureMemberRole.teacher,
                # Une affectation retiree ne donne plus acces a la salle.
                ClassroomTeacher.removed_at.is_(None),
            )
        ).scalars().all()
    )

    return classroom_ids


def can_access_classroom(db: Session, user_id: uuid.UUID, classroom_id: uuid.UUID) -> bool:
    """Raccourci pour les endpoints : True si l'utilisateur peut voir cette Classroom."""
    return classroom_id in get_accessible_classroom_ids(db, user_id)
