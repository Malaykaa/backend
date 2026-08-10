"""Add consent fields to users table.

Revision ID: 015_add_user_consent
Revises: 014
Create Date: 2026-08-09

Conformité Loi n° 2013-450 du 19 juin 2013 (CI) — art. 6 :
Enregistrement de la preuve du consentement explicite au traitement
des données personnelles et à l'utilisation de l'IA.
"""

from alembic import op
import sqlalchemy as sa

revision = "015_add_user_consent"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "consent_given_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp UTC du consentement explicite (Loi 2013-450 art.6)",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "consent_version",
            sa.String(20),
            nullable=True,
            comment="Version du document de consentement accepté (ex: v1.0)",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "consent_ip",
            sa.String(45),
            nullable=True,
            comment="Adresse IP au moment du consentement (IPv4/IPv6)",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "consent_ip")
    op.drop_column("users", "consent_version")
    op.drop_column("users", "consent_given_at")
