"""FeedbackRepository — CRUD + requêtes spécialisées pour UserOfferFeedback."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.user_offer_feedback import FeedbackAction, UserOfferFeedback
from app.repositories.base import BaseRepository

# Durée de validité du cache LLM en secondes (1 heure)
LLM_CACHE_TTL_SECONDS = 3_600


class FeedbackRepository(BaseRepository[UserOfferFeedback]):
    def __init__(self, db: Session) -> None:
        super().__init__(UserOfferFeedback, db)

    def get_user_feedbacks(
        self, user_id: uuid.UUID
    ) -> dict[str, FeedbackAction]:
        """Retourne toutes les actions de feedback d'un user.

        Clé = offer_ref, valeur = action la plus récente/prioritaire.
        Priorité : applied > saved > clicked > dismissed > ignored
        llm_validated et sent sont exclus : ce ne sont pas des signaux de
        pertinence (cache de score / marqueur d'envoi MatchRunner).
        """
        rows = self.db.execute(
            select(UserOfferFeedback)
            .where(
                UserOfferFeedback.user_id == user_id,
                UserOfferFeedback.action.not_in(
                    [FeedbackAction.llm_validated, FeedbackAction.sent]
                ),
            )
            .order_by(UserOfferFeedback.created_at.desc())
        ).scalars().all()

        # Priorité d'action par offre (la plus forte gagne)
        priority = {
            FeedbackAction.applied:   5,
            FeedbackAction.saved:     4,
            FeedbackAction.clicked:   3,
            FeedbackAction.dismissed: 2,
            FeedbackAction.ignored:   1,
        }

        result: dict[str, FeedbackAction] = {}
        for row in rows:
            ref = row.offer_ref
            if ref not in result:
                result[ref] = row.action
            else:
                # Garder l'action la plus prioritaire
                if priority.get(row.action, 0) > priority.get(result[ref], 0):
                    result[ref] = row.action
        return result

    def get_llm_score(
        self,
        user_id: uuid.UUID,
        offer_ref: str,
        intent_version: int,
    ) -> float | None:
        """Retourne le score LLM en cache si encore valide, sinon None."""
        row = self.db.execute(
            select(UserOfferFeedback)
            .where(
                UserOfferFeedback.user_id == user_id,
                UserOfferFeedback.offer_ref == offer_ref,
                UserOfferFeedback.action == FeedbackAction.llm_validated,
                UserOfferFeedback.intent_version == intent_version,
            )
        ).scalar_one_or_none()

        if not row:
            return None

        # Invalider si trop vieux
        age = (datetime.now(timezone.utc) - row.created_at).total_seconds()
        if age > LLM_CACHE_TTL_SECONDS:
            return None

        return row.llm_score

    def upsert_feedback(
        self,
        user_id: uuid.UUID,
        offer_ref: str,
        action: FeedbackAction,
        llm_score: float | None = None,
        intent_version: int | None = None,
    ) -> UserOfferFeedback:
        """Crée ou met à jour un feedback (upsert par user_id + offer_ref + action)."""
        existing = self.db.execute(
            select(UserOfferFeedback).where(
                UserOfferFeedback.user_id == user_id,
                UserOfferFeedback.offer_ref == offer_ref,
                UserOfferFeedback.action == action,
            )
        ).scalar_one_or_none()

        if existing:
            existing.llm_score = llm_score
            existing.intent_version = intent_version
            existing.created_at = datetime.now(timezone.utc)
            self.db.flush()
            return existing

        feedback = UserOfferFeedback(
            user_id=user_id,
            offer_ref=offer_ref,
            action=action,
            llm_score=llm_score,
            intent_version=intent_version,
        )
        self.db.add(feedback)
        self.db.flush()
        return feedback

    def save_llm_scores_batch(
        self,
        user_id: uuid.UUID,
        scores: list[tuple[str, float]],
        intent_version: int,
    ) -> None:
        """Sauvegarde en lot les scores LLM pour un ensemble d'offres.

        Efface d'abord les anciens scores LLM pour ce user (évite l'accumulation).
        """
        # Supprimer les anciens scores LLM pour ce user
        self.db.execute(
            delete(UserOfferFeedback).where(
                UserOfferFeedback.user_id == user_id,
                UserOfferFeedback.action == FeedbackAction.llm_validated,
            )
        )
        self.db.flush()

        # Insérer les nouveaux
        for offer_ref, llm_score in scores:
            feedback = UserOfferFeedback(
                user_id=user_id,
                offer_ref=offer_ref,
                action=FeedbackAction.llm_validated,
                llm_score=llm_score,
                intent_version=float(intent_version),
            )
            self.db.add(feedback)
        self.db.flush()

    def get_sent_offer_refs(self, user_id: uuid.UUID) -> set[str]:
        """Offer_refs déjà poussées à ce user via MatchRunner (action=sent).

        Utilisé pour exclure ces offres des prochains envois automatiques
        (chat + WhatsApp) — une offre n'est envoyée qu'une seule fois.
        """
        rows = self.db.execute(
            select(UserOfferFeedback.offer_ref).where(
                UserOfferFeedback.user_id == user_id,
                UserOfferFeedback.action == FeedbackAction.sent,
            )
        ).scalars().all()
        return set(rows)

    def mark_sent_batch(self, user_id: uuid.UUID, offer_refs: list[str]) -> None:
        """Marque ces offer_refs comme envoyées à ce user (idempotent).

        Appelé par MatchRunner juste après avoir poussé une sélection
        d'offres, pour qu'elles ne soient plus jamais resélectionnées.
        """
        if not offer_refs:
            return
        existing = self.get_sent_offer_refs(user_id)
        for ref in offer_refs:
            if ref in existing:
                continue
            self.db.add(UserOfferFeedback(
                user_id=user_id, offer_ref=ref, action=FeedbackAction.sent,
            ))
        self.db.flush()
