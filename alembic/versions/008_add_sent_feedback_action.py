"""Add 'sent' value to feedback_action_enum.

Revision ID: 008
Revises: 007
Create Date: 2026-06-21 00:00:00.000000
"""

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ne peut pas s'exécuter dans une transaction sur certaines
    # versions PostgreSQL — on utilise COMMIT implicite via op.execute.
    op.execute("ALTER TYPE feedback_action_enum ADD VALUE IF NOT EXISTS 'sent'")


def downgrade() -> None:
    # PostgreSQL ne supporte pas DROP VALUE sur un enum.
    # Le downgrade ne fait rien (les valeurs restent mais sont inoffensives).
    pass
