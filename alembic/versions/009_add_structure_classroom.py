"""Add Structure, StructureMember, Classroom (Malayka Institution — Phase 0).

Revision ID: 009
Revises: 008
Create Date: 2026-06-22 00:00:00.000000
"""

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ENUMs en SQL brut — évite que SQLAlchemy émette une deuxième CREATE TYPE
    # via son système d'événements before_create lors du CREATE TABLE qui suit
    # (même piège déjà rencontré dans g7b8c9d0e1f2_add_scraped_offers.py).
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE structure_status_enum AS ENUM ('pending', 'active', 'rejected');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE structure_member_role_enum AS ENUM ('super_admin', 'teacher');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS structures (
            id          UUID PRIMARY KEY,
            name        VARCHAR(200) NOT NULL,
            country     VARCHAR(100),
            status      structure_status_enum NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS structure_members (
            id            UUID PRIMARY KEY,
            structure_id  UUID NOT NULL REFERENCES structures(id) ON DELETE CASCADE,
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role          structure_member_role_enum NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_structure_member UNIQUE (structure_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_structure_members_structure_id "
        "ON structure_members (structure_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_structure_members_user_id "
        "ON structure_members (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classrooms (
            id            UUID PRIMARY KEY,
            structure_id  UUID NOT NULL REFERENCES structures(id) ON DELETE CASCADE,
            name          VARCHAR(200) NOT NULL,
            invite_code   VARCHAR(20) NOT NULL UNIQUE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classrooms_structure_id "
        "ON classrooms (structure_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classrooms_invite_code "
        "ON classrooms (invite_code)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS classrooms")
    op.execute("DROP TABLE IF EXISTS structure_members")
    op.execute("DROP TABLE IF EXISTS structures")
    op.execute("DROP TYPE IF EXISTS structure_member_role_enum")
    op.execute("DROP TYPE IF EXISTS structure_status_enum")
