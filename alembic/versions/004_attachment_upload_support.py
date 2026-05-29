"""attachment_upload_support

Revision ID: 004
Revises: 003
Create Date: 2026-05-23 00:00:00.000000

Rend message_id nullable (attachments "pending" uploadés avant le message),
ajoute pending_user_id pour ownership et extracted_text pour les PDFs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rendre message_id nullable
    op.alter_column("attachments", "message_id", nullable=True)

    # Ajouter pending_user_id
    op.add_column(
        "attachments",
        sa.Column("pending_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_attachments_pending_user_id", "attachments", ["pending_user_id"])

    # Ajouter extracted_text
    op.add_column(
        "attachments",
        sa.Column("extracted_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attachments", "extracted_text")
    op.drop_index("ix_attachments_pending_user_id", table_name="attachments")
    op.drop_column("attachments", "pending_user_id")
    op.alter_column("attachments", "message_id", nullable=False)
