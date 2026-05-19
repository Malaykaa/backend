"""add_profile_fields_and_phone_index

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7
Create Date: 2026-04-06 00:00:00.000000

Adds gender, birth_year, primary_role to profiles.
Adds partial unique index on users.phone (where phone IS NOT NULL).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nouveaux champs profil
    op.add_column("profiles", sa.Column("gender", sa.String(length=10), nullable=True))
    op.add_column("profiles", sa.Column("birth_year", sa.Integer(), nullable=True))
    op.add_column("profiles", sa.Column("primary_role", sa.String(length=30), nullable=True))

    # Index unique partiel sur phone (ignore les NULL)
    op.create_index(
        "ix_users_phone_unique",
        "users",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_phone_unique", table_name="users")
    op.drop_column("profiles", "primary_role")
    op.drop_column("profiles", "birth_year")
    op.drop_column("profiles", "gender")
