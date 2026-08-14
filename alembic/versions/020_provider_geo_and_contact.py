"""Provider location always required, portfolio field, request contact phone.

Corrige le sens du mode de prestation : c'est le CLIENT qui choisit, dans sa
demande, si la localisation compte — pas le prestataire, qui a toujours une
ville. `delivery_mode` n'avait donc rien à faire sur `service_providers`.

Revision ID: 020
Revises: 019
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("service_providers", "delivery_mode")
    op.add_column(
        "service_providers",
        sa.Column("portfolio", sa.Text(), nullable=True),
    )
    op.add_column(
        "service_requests",
        sa.Column("contact_phone", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_requests", "contact_phone")
    op.drop_column("service_providers", "portfolio")
    op.add_column(
        "service_providers",
        sa.Column(
            "delivery_mode", sa.Enum(name="delivery_mode_enum"),
            nullable=False, server_default="onsite",
        ),
    )
