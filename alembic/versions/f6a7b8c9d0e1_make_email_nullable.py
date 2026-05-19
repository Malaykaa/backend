"""Make user email nullable — phone users don't need synthetic emails.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Remove NOT NULL constraint on email
    op.alter_column("users", "email", nullable=True)

    # 2. Clean up existing synthetic emails (+digits@malaykaa.app or digits@malaykaa.app)
    op.execute(
        "UPDATE users SET email = NULL WHERE email ~ '^\\+?[0-9]+@malaykaa\\.app$'"
    )


def downgrade() -> None:
    # Restore synthetic emails for phone users that have NULL email
    op.execute(
        "UPDATE users SET email = phone || '@malaykaa.app' "
        "WHERE email IS NULL AND phone IS NOT NULL"
    )
    op.alter_column("users", "email", nullable=False)
