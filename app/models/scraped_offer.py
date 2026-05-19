"""Modèle ScrapedOffer — offres scrapées via Apify et Perplexity."""

import enum
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Boolean, DateTime, Enum, Float, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Dimension réelle du modèle pplx-embed-v1-4b : 2560 int8 quantifiés.
# Vérifié expérimentalement (base64 → 2560 octets → struct int8).
# La migration n4o5p6q7r8s9 redimensionne la colonne de 3416 → 2560.
EMBEDDING_DIM = 2560


class ScrapedOfferType(str, enum.Enum):
    job = "job"
    formation = "formation"
    grant = "grant"
    scholarship = "scholarship"
    partnership = "partnership"
    call_for_applications = "call_for_applications"
    opportunity = "opportunity"
    resource = "resource"


class ScrapedOffer(Base):
    __tablename__ = "scraped_offers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    offer_type: Mapped[ScrapedOfferType] = mapped_column(
        Enum(ScrapedOfferType), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str | None] = mapped_column(String(300), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    salary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    normalized_title: Mapped[str | None] = mapped_column(
        String(300), nullable=True, index=True
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Embedding sémantique — halfvec(2560) depuis la migration o5p6q7r8s9t0.
    # halfvec (float16) permet l'index HNSW jusqu'à 4000 dims (vs 2000 pour vector).
    # NULL si non encore généré — recalculé automatiquement par le pipeline de scraping.
    embedding: Mapped[list[float] | None] = mapped_column(
        HALFVEC(EMBEDDING_DIM), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_scraped_offer_source_eid"),
    )
