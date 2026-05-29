"""add user_notifications table

Revision ID: 006
Revises: 005
Create Date: 2026-05-27

Notifications in-app créées par MatchRunner quand une offre score >= 80%.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "offer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scraped_offers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("offer_title",    sa.String(500),  nullable=True),
        sa.Column("offer_url",      sa.String(2048), nullable=True),
        sa.Column("offer_type",     sa.String(50),   nullable=True),
        sa.Column("score_pct",      sa.Integer(),    nullable=True),
        sa.Column("seen",           sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
    )
    op.create_index(
        "ix_user_notifications_user_seen",
        "user_notifications",
        ["user_id", "seen"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_notifications_user_seen", table_name="user_notifications")
    op.drop_table("user_notifications")
