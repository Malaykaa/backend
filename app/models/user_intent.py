"""UserIntent — intention extraite par LLM depuis une conversation d'objectif.

Stockée après EXTRACTION_THRESHOLD messages pour alimenter le moteur de matching.
Une seule intention active par thread (upsert par thread_id).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserIntent(Base):
    __tablename__ = "user_intents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Une seule intention par thread — upsert à chaque re-extraction
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Intention extraite par le LLM ──────────────────────────────────────
    # Résumé libre : "Stage PFE en BTP, 6 mois, Afrique de l'Ouest"
    intent_summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Type normalisé pour le matching
    # Valeurs : stage | emploi | bourse | financement | appel_offre |
    #           formation | reconversion | entrepreneuriat | partenariat | autre
    intent_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Domaine/secteur : BTP, informatique, finance, santé, agriculture...
    domain: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Mots-clés pour le matching : ["PFE", "génie civil", "BTP", "stage"]
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Localisation souhaitée : pays, région, ville
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Niveau : licence, master, doctorat, PFE, junior, senior...
    level: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Durée : 6 mois, 1 an, CDI, CDD...
    duration: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # JSON brut retourné par le LLM — pour debug et évolutions futures
    raw_structured: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ── Méta ────────────────────────────────────────────────────────────────
    # Nombre de messages au moment de l'extraction (pour calculer le prochain seuil)
    message_count_at_extraction: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Incrément à chaque re-extraction (v1, v2, v3...)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relations (lecture seule — pas de cascade write depuis ici) ─────────
    user: Mapped["User"] = relationship("User")  # noqa: F821
    thread: Mapped["ChatThread"] = relationship("ChatThread")  # noqa: F821
