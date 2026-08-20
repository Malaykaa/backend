"""Add CareerReference (référentiel curaté métiers/compétences/formations).

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
        CREATE TABLE IF NOT EXISTS career_references (
            id                    UUID PRIMARY KEY,
            title                 VARCHAR(200) NOT NULL,
            category              VARCHAR(100),
            description           TEXT,
            key_skills            JSON,
            example_formations    JSON,
            country               VARCHAR(10),
            source_note           TEXT,
            reviewed_by           UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at           TIMESTAMPTZ,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_career_references_category "
        "ON career_references (category)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_career_references_country "
        "ON career_references (country)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS career_references")
