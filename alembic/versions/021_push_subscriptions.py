"""Add push_subscriptions table for Web Push (PWA).

Revision ID: 021
Revises: 020
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
