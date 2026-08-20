"""Add interests + self_description to profiles.

Revision ID: 023
Revises: 022
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column(
            "interests",
            sa.JSON(),
            nullable=True,
            comment="Intérêts déclarés (tags libres) — demandés à la création d'objectif.",
        ),
    )
    op.add_column(
        "profiles",
        sa.Column(
            "self_description",
            sa.Text(),
            nullable=True,
            comment="Description libre de l'utilisateur — jamais sollicitée sur le "
            "comportement ou la situation familiale directement.",
        ),
    )


def downgrade() -> None:
    op.drop_column("profiles", "self_description")
    op.drop_column("profiles", "interests")
