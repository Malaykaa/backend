"""Add per-user matching preferences on profiles.

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
Create Date: 2026-05-06 10:00:00.000000

Champs ajoutés :
- match_frequency_hours : intervalle minimum (heures) entre deux runs de
  matching automatique pour cet utilisateur. NULL = utilise le défaut
  applicatif (6 h). Permet de respecter les préférences (ex. 24 h pour
  un user qui ne veut qu'un digest quotidien).
- match_last_run_at : timestamp du dernier run réussi pour cet user.
  NULL = jamais. Le scheduler s'en sert pour décider qui est éligible.
- match_notifications_enabled : opt-out (default TRUE). Quand FALSE,
  on ne crée plus de message dans le chat ni de notif WhatsApp.
"""

import sqlalchemy as sa
from alembic import op

revision = "l3m4n5o6p7q8"
down_revision = "k2l3m4n5o6p7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("match_frequency_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "profiles",
        sa.Column(
            "match_last_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "profiles",
        sa.Column(
            "match_notifications_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
    )


def downgrade() -> None:
    op.drop_column("profiles", "match_notifications_enabled")
    op.drop_column("profiles", "match_last_run_at")
    op.drop_column("profiles", "match_frequency_hours")
