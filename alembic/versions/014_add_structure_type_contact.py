"""Add structure_type + contact fields to Structure (Malayka Institution).

Revision ID: 014
Revises: 013
Create Date: 2026-06-23 00:00:00.000000
"""

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE structure_type_enum AS ENUM (
                'training_center', 'independent_trainer', 'school', 'university', 'other'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute("ALTER TABLE structures ADD COLUMN IF NOT EXISTS structure_type structure_type_enum")
    op.execute("ALTER TABLE structures ADD COLUMN IF NOT EXISTS structure_type_other VARCHAR(200)")
    op.execute("ALTER TABLE structures ADD COLUMN IF NOT EXISTS address VARCHAR(300)")
    op.execute("ALTER TABLE structures ADD COLUMN IF NOT EXISTS email VARCHAR(320)")


def downgrade() -> None:
    op.execute("ALTER TABLE structures DROP COLUMN IF EXISTS email")
    op.execute("ALTER TABLE structures DROP COLUMN IF EXISTS address")
    op.execute("ALTER TABLE structures DROP COLUMN IF EXISTS structure_type_other")
    op.execute("ALTER TABLE structures DROP COLUMN IF EXISTS structure_type")
    op.execute("DROP TYPE IF EXISTS structure_type_enum")
