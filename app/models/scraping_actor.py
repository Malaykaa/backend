"""Modèle ScrapingActor — actors Apify gérés dynamiquement depuis l'admin."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Types de normalizer disponibles — correspondent aux fonctions dans apify_service.py
NORMALIZER_TYPES = (
    "indeed",
    "linkedin_job",
    "linkedin_post",
    "google_jobs",
    "facebook",
    "web",
)

# Modes d'exécution
RUN_MODES = ("light", "heavy", "both")


class ScrapingActor(Base):
    __tablename__ = "scraping_actors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    offer_type: Mapped[str] = mapped_column(String(50), nullable=False, default="opportunity")
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalizer_type: Mapped[str] = mapped_column(String(50), nullable=False, default="web")
    input_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    run_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="both")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
