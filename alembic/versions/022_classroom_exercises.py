"""Add ClassroomExercise, ClassroomExerciseQuestion, ClassroomExerciseRecipient
(Teacher Copilot — exercices/évaluations QCM).

Revision ID: 022
Revises: 021
Create Date: 2026-08-20 00:00:00.000000
"""

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE classroom_exercise_kind_enum AS ENUM ('exercise', 'evaluation');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_exercises (
            id                   UUID PRIMARY KEY,
            classroom_id         UUID NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
            created_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            title                VARCHAR(300) NOT NULL,
            subject              VARCHAR(100),
            kind                 classroom_exercise_kind_enum NOT NULL DEFAULT 'exercise',
            topic_hint           VARCHAR(300),
            instructions         TEXT,
            source_course_id     UUID REFERENCES classroom_courses(id) ON DELETE SET NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_exercises_classroom_id "
        "ON classroom_exercises (classroom_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_exercise_questions (
            id                      UUID PRIMARY KEY,
            exercise_id             UUID NOT NULL REFERENCES classroom_exercises(id) ON DELETE CASCADE,
            prompt                  TEXT NOT NULL,
            choices                 JSON NOT NULL,
            correct_choice_index    INTEGER NOT NULL,
            explanation             TEXT,
            points                  INTEGER NOT NULL DEFAULT 1,
            "order"                 INTEGER NOT NULL,
            topic_tag               VARCHAR(100)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_exercise_questions_exercise_id "
        "ON classroom_exercise_questions (exercise_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_exercise_recipients (
            id               UUID PRIMARY KEY,
            exercise_id      UUID NOT NULL REFERENCES classroom_exercises(id) ON DELETE CASCADE,
            user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_thread_id   UUID REFERENCES chat_threads(id) ON DELETE SET NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_classroom_exercise_recipient UNIQUE (exercise_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_exercise_recipients_exercise_id "
        "ON classroom_exercise_recipients (exercise_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_exercise_recipients_user_id "
        "ON classroom_exercise_recipients (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS classroom_exercise_recipients")
    op.execute("DROP TABLE IF EXISTS classroom_exercise_questions")
    op.execute("DROP TABLE IF EXISTS classroom_exercises")
    op.execute("DROP TYPE IF EXISTS classroom_exercise_kind_enum")
