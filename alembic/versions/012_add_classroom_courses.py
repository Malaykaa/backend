"""Add ClassroomCourse, ClassroomCourseStep, ClassroomCourseRecipient,
ClassroomCourseStepProgress (Malayka Institution — Phase 3).

Revision ID: 012
Revises: 011
Create Date: 2026-06-22 00:00:00.000000
"""

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE classroom_step_status_enum AS ENUM ('todo', 'in_progress', 'done');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_courses (
            id                   UUID PRIMARY KEY,
            classroom_id         UUID NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
            created_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            title                VARCHAR(300) NOT NULL,
            raw_content          TEXT,
            attachment_id        UUID REFERENCES attachments(id) ON DELETE SET NULL,
            summary              TEXT NOT NULL,
            explanation          TEXT NOT NULL,
            sources              JSON,
            suggestions          JSON,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_courses_classroom_id "
        "ON classroom_courses (classroom_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_course_steps (
            id           UUID PRIMARY KEY,
            course_id    UUID NOT NULL REFERENCES classroom_courses(id) ON DELETE CASCADE,
            label        VARCHAR(300) NOT NULL,
            description  TEXT NOT NULL,
            "order"      INTEGER NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_course_steps_course_id "
        "ON classroom_course_steps (course_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_course_recipients (
            id               UUID PRIMARY KEY,
            course_id        UUID NOT NULL REFERENCES classroom_courses(id) ON DELETE CASCADE,
            user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_thread_id   UUID REFERENCES chat_threads(id) ON DELETE SET NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_classroom_course_recipient UNIQUE (course_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_course_recipients_course_id "
        "ON classroom_course_recipients (course_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_course_recipients_user_id "
        "ON classroom_course_recipients (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_course_step_progress (
            id             UUID PRIMARY KEY,
            recipient_id   UUID NOT NULL REFERENCES classroom_course_recipients(id) ON DELETE CASCADE,
            step_id        UUID NOT NULL REFERENCES classroom_course_steps(id) ON DELETE CASCADE,
            status         classroom_step_status_enum NOT NULL,
            completed_at   TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_classroom_step_progress UNIQUE (recipient_id, step_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_course_step_progress_recipient_id "
        "ON classroom_course_step_progress (recipient_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_course_step_progress_step_id "
        "ON classroom_course_step_progress (step_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS classroom_course_step_progress")
    op.execute("DROP TABLE IF EXISTS classroom_course_recipients")
    op.execute("DROP TABLE IF EXISTS classroom_course_steps")
    op.execute("DROP TABLE IF EXISTS classroom_courses")
    op.execute("DROP TYPE IF EXISTS classroom_step_status_enum")
