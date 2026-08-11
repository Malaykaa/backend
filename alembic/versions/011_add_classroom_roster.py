"""Add ClassroomRosterEntry, ClassroomMembership (Malayka Institution — Phase 2).

Revision ID: 011
Revises: 010
Create Date: 2026-06-22 00:00:00.000000
"""

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE membership_status_enum AS ENUM ('accepted', 'pending_review', 'rejected');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_roster_entries (
            id                   UUID PRIMARY KEY,
            classroom_id         UUID NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
            first_name           VARCHAR(100) NOT NULL,
            last_name            VARCHAR(100) NOT NULL,
            normalized_name      VARCHAR(220) NOT NULL,
            claimed_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            claimed_at           TIMESTAMPTZ,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_roster_entries_classroom_id "
        "ON classroom_roster_entries (classroom_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_roster_entries_normalized_name "
        "ON classroom_roster_entries (normalized_name)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_memberships (
            id                      UUID PRIMARY KEY,
            classroom_id            UUID NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
            user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            requested_first_name    VARCHAR(100) NOT NULL,
            requested_last_name     VARCHAR(100) NOT NULL,
            status                  membership_status_enum NOT NULL,
            roster_entry_id         UUID REFERENCES classroom_roster_entries(id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_classroom_membership UNIQUE (classroom_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_memberships_classroom_id "
        "ON classroom_memberships (classroom_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_memberships_user_id "
        "ON classroom_memberships (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS classroom_memberships")
    op.execute("DROP TABLE IF EXISTS classroom_roster_entries")
    op.execute("DROP TYPE IF EXISTS membership_status_enum")
