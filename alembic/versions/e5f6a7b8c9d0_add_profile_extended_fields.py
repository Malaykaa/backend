"""add_profile_extended_fields

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-11 00:00:00.000000

Adds city, language, field_of_study, goals, preferred_content to profiles.
These fields are sent by the frontend (UserSettingsPanel) and were previously
silently dropped, causing data loss.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("profiles", sa.Column("language", sa.String(length=10), nullable=True, server_default="fr"))
    op.add_column("profiles", sa.Column("field_of_study", sa.String(length=200), nullable=True))
    op.add_column("profiles", sa.Column("goals", sa.JSON(), nullable=True))
    op.add_column("profiles", sa.Column("preferred_content", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "preferred_content")
    op.drop_column("profiles", "goals")
    op.drop_column("profiles", "field_of_study")
    op.drop_column("profiles", "language")
    op.drop_column("profiles", "city")
