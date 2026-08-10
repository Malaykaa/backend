"""Add last_read_at to chat_threads for unread message tracking.

Revision ID: 016
Revises: 015_add_user_consent
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015_add_user_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_threads",
        sa.Column(
            "last_read_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Dernière fois que l'utilisateur a ouvert ce thread (pour le calcul des non-lus)",
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_threads", "last_read_at")
