"""Add delivery_mode to service providers and requests.

Revision ID: 019
Revises: 018
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN CREATE TYPE delivery_mode_enum AS ENUM "
        "('remote','onsite','hybrid'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    # server_default 'onsite' : les vitrines et demandes déjà en base avaient
    # toutes une ville renseignée en pratique — c'est la valeur qui préserve
    # exactement le comportement observé avant l'ajout du champ.
    op.add_column(
        "service_providers",
        sa.Column(
            "delivery_mode", postgresql.ENUM(name="delivery_mode_enum", create_type=False),
            nullable=False, server_default="onsite",
        ),
    )
    op.add_column(
        "service_requests",
        sa.Column(
            "delivery_mode", postgresql.ENUM(name="delivery_mode_enum", create_type=False),
            nullable=False, server_default="onsite",
        ),
    )


def downgrade() -> None:
    op.drop_column("service_requests", "delivery_mode")
    op.drop_column("service_providers", "delivery_mode")
    op.execute("DROP TYPE IF EXISTS delivery_mode_enum")
