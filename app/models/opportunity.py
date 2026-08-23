"""Modeles Opportunity / UserOpportunity.

OBSOLETE — remplace par ScrapedOffer / recommendations_router.

Deux systemes d'opportunites coexistent dans l'application :

- Opportunity / UserOpportunity (ce module) : generation historique, scoring
  par regles fixes (domaine + pays + statut, puis bonus d'intention).
- ScrapedOffer (app/models/scraped_offer.py) : le systeme actuel — collecte
  automatique, recherche semantique pgvector, rescoring LLM, retours
  utilisateur. C'est LUI qui alimente le fil "Pour toi" et les alertes.

En cas de doute sur la table faisant autorite pour les opportunites, c'est
ScrapedOffer. Ne pas ajouter de fonctionnalite ici.

Conserve et non supprime : verifie, le frontend n'appelle jamais /opportunities
(seule occurrence du mot : une categorie de scraping sans rapport). Mais des
donnees peuvent subsister en base et un eventuel client externe n'est pas
verifiable depuis le code. La suppression se fera a froid, une fois cette
derniere incertitude levee.
"""

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OpportunityType(str, enum.Enum):
    job = "job"
    grant = "grant"
    scholarship = "scholarship"
    tender = "tender"
    internship = "internship"
    funding = "funding"


class UserOpportunityStatus(str, enum.Enum):
    new = "new"
    viewed = "viewed"
    applied = "applied"


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[OpportunityType] = mapped_column(Enum(OpportunityType), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relations
    user_opportunities: Mapped[list["UserOpportunity"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )


class UserOpportunity(Base):
    __tablename__ = "user_opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[UserOpportunityStatus] = mapped_column(
        Enum(UserOpportunityStatus), nullable=False, default=UserOpportunityStatus.new
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relations
    user: Mapped["User"] = relationship(back_populates="user_opportunities")  # noqa: F821
    opportunity: Mapped["Opportunity"] = relationship(back_populates="user_opportunities")
