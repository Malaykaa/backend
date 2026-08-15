"""Abonnements push web (PWA) — un navigateur/appareil par ligne.

Un même utilisateur peut avoir plusieurs abonnements (téléphone, ordinateur,
plusieurs navigateurs) : chacun reçoit sa propre notification, indépendamment
des autres.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Identifiant unique fourni par le navigateur — sert aussi de clé de
    # dédoublonnage : re-souscrire depuis le même appareil met à jour la ligne
    # existante plutôt que d'en créer une seconde.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
