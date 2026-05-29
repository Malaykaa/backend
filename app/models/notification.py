"""Notifications in-app pour les offres matchées par MatchRunner."""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserNotification(Base):
    __tablename__ = "user_notifications"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Référence optionnelle à l'offre source (nullable car elle peut être supprimée)
    offer_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scraped_offers.id", ondelete="SET NULL"),
        nullable=True,
    )
    offer_title: Mapped[str | None]    = mapped_column(String(500),  nullable=True)
    offer_url:   Mapped[str | None]    = mapped_column(String(2048), nullable=True)
    offer_type:  Mapped[str | None]    = mapped_column(String(50),   nullable=True)
    score_pct:   Mapped[int | None]    = mapped_column(Integer,      nullable=True)  # 0-99
    seen:        Mapped[bool]          = mapped_column(Boolean,      nullable=False, default=False)
    created_at:  Mapped[DateTime]      = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    user = relationship("User", back_populates="notifications", lazy="raise")
