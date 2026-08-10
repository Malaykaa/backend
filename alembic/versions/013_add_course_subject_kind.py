"""Add subject + kind to ClassroomCourse (Malayka Institution — Phase 4).

Revision ID: 013
Revises: 012
Create Date: 2026-06-22 00:00:00.000000
"""

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE classroom_course_kind_enum AS ENUM ('course', 'evolution_plan');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute("ALTER TABLE classroom_courses ADD COLUMN IF NOT EXISTS subject VARCHAR(100)")
    op.execute(
        "ALTER TABLE classroom_courses ADD COLUMN IF NOT EXISTS kind "
        "classroom_course_kind_enum NOT NULL DEFAULT 'course'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE classroom_courses DROP COLUMN IF EXISTS kind")
    op.execute("ALTER TABLE classroom_courses DROP COLUMN IF EXISTS subject")
    op.execute("DROP TYPE IF EXISTS classroom_course_kind_enum")
