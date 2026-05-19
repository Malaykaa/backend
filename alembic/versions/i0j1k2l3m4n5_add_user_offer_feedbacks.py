"""Add user_offer_feedbacks table — feedback utilisateur + cache scores LLM.

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-04-23 00:00:00.000000
"""

from alembic import op

revision = "i0j1k2l3m4n5"
down_revision = "h9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enum pour les actions de feedback
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE feedback_action_enum AS ENUM (
                'clicked', 'saved', 'applied',
                'ignored', 'dismissed', 'llm_validated'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_offer_feedbacks (
            id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID            NOT NULL,
            offer_ref       VARCHAR(600)    NOT NULL,
            action          feedback_action_enum NOT NULL,
            llm_score       FLOAT,
            intent_version  FLOAT,
            created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

            CONSTRAINT uq_user_offer_feedback
                UNIQUE (user_id, offer_ref, action)
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_offer_feedbacks_user_id "
        "ON user_offer_feedbacks(user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_offer_feedbacks_offer_ref "
        "ON user_offer_feedbacks(offer_ref)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_offer_feedbacks_user_action "
        "ON user_offer_feedbacks(user_id, action)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_offer_feedbacks")
    op.execute("DROP TYPE IF EXISTS feedback_action_enum")
