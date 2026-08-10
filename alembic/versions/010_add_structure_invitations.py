"""Add ClassroomTeacher, StructureInvitation, StructureInvitationClassroom
(Malayka Institution — Phase 1).

Revision ID: 010
Revises: 009
Create Date: 2026-06-22 00:00:00.000000
"""

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE invitation_status_enum AS ENUM
                ('pending', 'accepted', 'pending_review', 'rejected', 'expired');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_teachers (
            id                    UUID PRIMARY KEY,
            classroom_id          UUID NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
            structure_member_id   UUID NOT NULL REFERENCES structure_members(id) ON DELETE CASCADE,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_classroom_teacher UNIQUE (classroom_id, structure_member_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_teachers_classroom_id "
        "ON classroom_teachers (classroom_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_teachers_structure_member_id "
        "ON classroom_teachers (structure_member_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS structure_invitations (
            id                  UUID PRIMARY KEY,
            structure_id        UUID NOT NULL REFERENCES structures(id) ON DELETE CASCADE,
            first_name          VARCHAR(100) NOT NULL,
            last_name           VARCHAR(100) NOT NULL,
            normalized_name     VARCHAR(220) NOT NULL,
            contact             VARCHAR(320),
            token               VARCHAR(64) NOT NULL UNIQUE,
            status              invitation_status_enum NOT NULL,
            created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            accepted_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at          TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_structure_invitations_structure_id "
        "ON structure_invitations (structure_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_structure_invitations_token "
        "ON structure_invitations (token)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_structure_invitations_normalized_name "
        "ON structure_invitations (normalized_name)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS structure_invitation_classrooms (
            id              UUID PRIMARY KEY,
            invitation_id   UUID NOT NULL REFERENCES structure_invitations(id) ON DELETE CASCADE,
            classroom_id    UUID NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
            CONSTRAINT uq_invitation_classroom UNIQUE (invitation_id, classroom_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_structure_invitation_classrooms_invitation_id "
        "ON structure_invitation_classrooms (invitation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_structure_invitation_classrooms_classroom_id "
        "ON structure_invitation_classrooms (classroom_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS structure_invitation_classrooms")
    op.execute("DROP TABLE IF EXISTS structure_invitations")
    op.execute("DROP TABLE IF EXISTS classroom_teachers")
    op.execute("DROP TYPE IF EXISTS invitation_status_enum")
