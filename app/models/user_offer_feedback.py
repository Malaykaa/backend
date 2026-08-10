"""UserOfferFeedback — tracking des interactions utilisateur sur les recommandations.

Sert à deux fins :
1. Signal comportemental (clicked/saved/ignored/applied) → ajuste le score de matching
2. Cache des scores LLM (action=llm_validated) → évite de re-appeler le LLM à chaque requête
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FeedbackAction(str, enum.Enum):
    clicked      = "clicked"       # L'user a cliqué sur la recommandation
    saved        = "saved"         # L'user a sauvegardé / mis en favori
    applied      = "applied"       # L'user a postulé / soumis une candidature
    ignored      = "ignored"       # L'user a explicitement ignoré
    dismissed    = "dismissed"     # L'user a fermé / fait disparaître
    llm_validated = "llm_validated"  # Score calculé par le LLM (pas une action user)
    sent          = "sent"         # Déjà poussée par MatchRunner (chat/WhatsApp) — pas une action user


# Impact de chaque action sur le score final (delta en points)
FEEDBACK_SCORE_DELTA: dict[FeedbackAction, float] = {
    FeedbackAction.clicked:       +15.0,
    FeedbackAction.saved:         +20.0,
    FeedbackAction.applied:         0.0,  # filtré complètement (déjà postulé)
    FeedbackAction.ignored:       -25.0,
    FeedbackAction.dismissed:     -20.0,
    FeedbackAction.llm_validated:   0.0,  # géré via llm_score séparément
    FeedbackAction.sent:            0.0,  # géré via exclusion dure dans MatchRunner
}


class UserOfferFeedback(Base):
    __tablename__ = "user_offer_feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Référence universelle à une offre : "scraped:{uuid}" ou "opportunity:{uuid}"
    offer_ref: Mapped[str] = mapped_column(String(600), nullable=False, index=True)

    action: Mapped[FeedbackAction] = mapped_column(
        Enum(FeedbackAction, name="feedback_action_enum"), nullable=False
    )

    # Uniquement renseigné pour action=llm_validated (0.0 à 10.0)
    llm_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Version de l'intention au moment du score LLM — permet d'invalider le cache
    intent_version: Mapped[int | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Un seul enregistrement par (user, offre, action)
        # Sauf llm_validated qui peut être mis à jour
        UniqueConstraint(
            "user_id", "offer_ref", "action",
            name="uq_user_offer_feedback",
        ),
    )
