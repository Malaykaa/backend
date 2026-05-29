"""add scraping_actors table

Revision ID: 003
Revises: 002
Create Date: 2026-05-21

Table pour gérer dynamiquement les actors Apify depuis le backoffice admin.
Chaque ligne = 1 actor avec son input_json, son normalizer et son mode d'exécution.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scraping_actors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("offer_type", sa.String(50), nullable=False, server_default="opportunity"),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("normalizer_type", sa.String(50), nullable=False, server_default="web"),
        sa.Column("input_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("run_mode", sa.String(20), nullable=False, server_default="both"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_scraping_actors_is_active", "scraping_actors", ["is_active"])
    op.create_index("ix_scraping_actors_run_mode", "scraping_actors", ["run_mode"])


def downgrade() -> None:
    op.drop_index("ix_scraping_actors_run_mode", table_name="scraping_actors")
    op.drop_index("ix_scraping_actors_is_active", table_name="scraping_actors")
    op.drop_table("scraping_actors")
