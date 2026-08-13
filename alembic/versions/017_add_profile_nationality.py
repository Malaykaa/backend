"""Add nationality to profiles.

Revision ID: 017
Revises: 016
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column(
            "nationality",
            sa.String(100),
            nullable=True,
            comment="Nationalité déclarée — distincte du pays de résidence (country).",
        ),
    )


def downgrade() -> None:
    op.drop_column("profiles", "nationality")
