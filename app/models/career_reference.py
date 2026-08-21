"""CareerReference — référentiel curaté métier ↔ compétences ↔ formations.

Contrairement à ScrapedOffer (collecté automatiquement), ce référentiel est
volontairement petit et curaté à la main : aucune source ivoirienne ou
régionale structurée n'existe (spike de recherche vérifié), donc pas de
prétention à l'exhaustivité automatisée. reviewed_by/reviewed_at NULL = premier
jet IA non validé — ne devrait pas atteindre la production sans relecture
humaine (cf. source_note pour la provenance du contenu).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CareerReference(Base):
    __tablename__ = "career_references"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    example_formations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # "CI" = Côte d'Ivoire ; NULL = générique, réservé à une extension régionale future.
    country: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
