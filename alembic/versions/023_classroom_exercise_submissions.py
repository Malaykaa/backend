"""Add ClassroomExerciseSubmission, ClassroomExerciseAnswer
(Teacher Copilot — soumissions et correction déterministe QCM).

Revision ID: 023
Revises: 022
Create Date: 2026-08-20 00:00:00.000001
"""

from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE exercise_submission_status_enum AS ENUM ('in_progress', 'submitted');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_exercise_submissions (
            id                UUID PRIMARY KEY,
            recipient_id      UUID NOT NULL REFERENCES classroom_exercise_recipients(id) ON DELETE CASCADE,
            attempt_number    INTEGER NOT NULL,
            status            exercise_submission_status_enum NOT NULL DEFAULT 'in_progress',
            score_points      INTEGER,
            max_points        INTEGER,
            score_pct         INTEGER,
            started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            submitted_at      TIMESTAMPTZ,
            CONSTRAINT uq_classroom_exercise_submission_attempt UNIQUE (recipient_id, attempt_number)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_exercise_submissions_recipient_id "
        "ON classroom_exercise_submissions (recipient_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS classroom_exercise_answers (
            id                       UUID PRIMARY KEY,
            submission_id            UUID NOT NULL REFERENCES classroom_exercise_submissions(id) ON DELETE CASCADE,
            question_id              UUID NOT NULL REFERENCES classroom_exercise_questions(id) ON DELETE CASCADE,
            selected_choice_index    INTEGER,
            is_correct               BOOLEAN NOT NULL DEFAULT FALSE,
            points_earned            INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT uq_classroom_exercise_answer UNIQUE (submission_id, question_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_exercise_answers_submission_id "
        "ON classroom_exercise_answers (submission_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classroom_exercise_answers_question_id "
        "ON classroom_exercise_answers (question_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS classroom_exercise_answers")
    op.execute("DROP TABLE IF EXISTS classroom_exercise_submissions")
    op.execute("DROP TYPE IF EXISTS exercise_submission_status_enum")
