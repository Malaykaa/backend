"""Add 'orientation' and 'coursework' values to goaltype enum.

Revision ID: 007
Revises: 006
Create Date: 2026-06-21 00:00:00.000000
"""

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ne peut pas s'exécuter dans une transaction sur certaines
    # versions PostgreSQL — on utilise COMMIT implicite via op.execute.
    op.execute("ALTER TYPE goaltype ADD VALUE IF NOT EXISTS 'orientation'")
    op.execute("ALTER TYPE goaltype ADD VALUE IF NOT EXISTS 'coursework'")


def downgrade() -> None:
    # PostgreSQL ne supporte pas DROP VALUE sur un enum.
    # Le downgrade ne fait rien (les valeurs restent mais sont inoffensives).
    pass
