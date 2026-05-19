"""Add user_intents table — stockage des intentions extraites par LLM.

Revision ID: h9i0j1k2l3m4
Revises: g7b8c9d0e1f2
Create Date: 2026-04-23 00:00:00.000000
"""

from alembic import op

revision = "h9i0j1k2l3m4"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_intents (
            id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID            NOT NULL REFERENCES users(id)        ON DELETE CASCADE,
            thread_id   UUID            NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            goal_id     UUID                     REFERENCES goals(id)        ON DELETE SET NULL,

            -- Intention extraite
            intent_summary  TEXT        NOT NULL,
            intent_type     VARCHAR(100),
            domain          VARCHAR(200),
            keywords        JSONB,
            location        VARCHAR(200),
            level           VARCHAR(100),
            duration        VARCHAR(100),
            raw_structured  JSONB,

            -- Méta
            message_count_at_extraction INTEGER NOT NULL DEFAULT 0,
            version         INTEGER     NOT NULL DEFAULT 1,
            extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            -- Contrainte : une seule intention active par thread
            CONSTRAINT uq_user_intents_thread_id UNIQUE (thread_id)
        )
        """
    )

    # Index sur user_id pour les requêtes "toutes intentions d'un user"
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_intents_user_id "
        "ON user_intents(user_id)"
    )

    # Index sur goal_id pour joindre avec les objectifs
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_intents_goal_id "
        "ON user_intents(goal_id)"
    )

    # Index sur extracted_at pour le tri chronologique
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_intents_extracted_at "
        "ON user_intents(extracted_at DESC)"
    )

    # Index GIN sur keywords (JSONB) pour les recherches full-text futures
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_intents_keywords_gin "
        "ON user_intents USING GIN (keywords)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_intents")
