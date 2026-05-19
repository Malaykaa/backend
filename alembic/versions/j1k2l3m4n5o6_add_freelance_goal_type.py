"""Add 'freelance' value to goaltype enum + topic20 preset mapping.

Revision ID: j1k2l3m4n5o6
Revises: i0j1k2l3m4n5
Create Date: 2026-04-24 00:00:00.000000
"""

from alembic import op

revision = "j1k2l3m4n5o6"
down_revision = "i0j1k2l3m4n5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ne peut pas s'exécuter dans une transaction sur certaines
    # versions PostgreSQL — on utilise COMMIT implicite via op.execute.
    op.execute("ALTER TYPE goaltype ADD VALUE IF NOT EXISTS 'freelance'")


def downgrade() -> None:
    # PostgreSQL ne supporte pas DROP VALUE sur un enum.
    # Le downgrade ne fait rien (la valeur reste mais est inoffensive).
    pass
