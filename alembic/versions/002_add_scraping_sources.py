"""add scraping_sources table

Revision ID: 002
Revises: 001
Create Date: 2026-05-21

Table pour gérer dynamiquement les sources web du scraper Apify
depuis le backoffice admin — sans modifier le code.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "002"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scraping_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.String(500), nullable=False, unique=True),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_scraping_sources_category", "scraping_sources", ["category"])
    op.create_index("ix_scraping_sources_is_active", "scraping_sources", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_scraping_sources_is_active", table_name="scraping_sources")
    op.drop_index("ix_scraping_sources_category", table_name="scraping_sources")
    op.drop_table("scraping_sources")
