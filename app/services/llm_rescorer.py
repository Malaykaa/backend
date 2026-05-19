"""LLMRescorer — validation LLM en lot des meilleures offres vs intention utilisateur.

Envoie un prompt batch unique avec toutes les offres candidates et l'intention
extraite. Le LLM note chaque offre de 0 à 10. Les scores sont mis en cache via
FeedbackRepository (TTL 1h) pour éviter des appels répétés.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.orm import Session

from app.models.user_intent import UserIntent
from app.repositories.feedback_repo import FeedbackRepository

logger = logging.getLogger(__name__)

# Candidats max envoyés au LLM par batch
MAX_CANDIDATES = 12
# Score heuristique minimum pour entrer dans le batch LLM
RESCORE_THRESHOLD = 20.0
# Conversion : llm_score (0-10) → bonus pts. Score 5 = neutre, 10 = +20 pts, 0 = -20 pts.
LLM_BONUS_SCALE = 4.0  # (score - 5) * LLM_BONUS_SCALE


class LLMRescorer:
    def __init__(self, db: Session) -> None:
        self._repo = FeedbackRepository(db)

    async def rescore_batch(
        self,
        user_id: uuid.UUID,
        candidates: list[dict],
        intent: UserIntent,
        llm,
    ) -> dict[str, float]:
        """Retourne dict[offer_ref → llm_score (0–10)] pour les top candidats.

        Vérifie le cache d'abord. Appelle le LLM uniquement pour les cache-miss.
        Les nouveaux scores sont sauvegardés dans user_offer_feedbacks.
        """
        if not candidates or not intent.intent_summary:
            return {}

        eligible = [
            c for c in candidates
            if c.get("base_score", 0) >= RESCORE_THRESHOLD
        ][:MAX_CANDIDATES]

        if not eligible:
            return {}

        cached: dict[str, float] = {}
        to_score: list[dict] = []

        for c in eligible:
            ref = c["offer_ref"]
            score = self._repo.get_llm_score(
                user_id=user_id,
                offer_ref=ref,
                intent_version=intent.version,
            )
            if score is not None:
                cached[ref] = score
            else:
                to_score.append(c)

        if not to_score:
            return cached

        fresh = await self._call_llm(to_score, intent, llm)

        if fresh:
            self._repo.save_llm_scores_batch(
                user_id=user_id,
                scores=list(fresh.items()),
                intent_version=intent.version,
            )

        return {**cached, **fresh}

    async def _call_llm(
        self,
        candidates: list[dict],
        intent: UserIntent,
        llm,
    ) -> dict[str, float]:
        lines = []
        for i, c in enumerate(candidates, 1):
            ref = c["offer_ref"]
            lines.append(
                f'{i}. [ref:{ref}] "{c.get("title","")}" — '
                f'{c.get("company","")} — {c.get("location","")} — type:{c.get("type","")}'
            )

        prompt = (
            f"Tu es un moteur de matching emploi/opportunités.\n\n"
            f"Intention utilisateur : {intent.intent_summary}\n"
            f"Type recherché : {intent.intent_type or 'non précisé'}\n"
            f"Domaine : {intent.domain or 'non précisé'}\n"
            f"Niveau : {intent.level or 'non précisé'}\n"
            f"Localisation : {intent.location or 'non précisée'}\n"
            f"Mots-clés : {', '.join(intent.keywords or [])}\n\n"
            f"Offres candidates :\n" + "\n".join(lines) + "\n\n"
            f"Pour chaque offre, donne un score de pertinence de 0 à 10 (10 = parfait match).\n"
            f"Réponds UNIQUEMENT avec un objet JSON : "
            f'{{\"ref:xxx\": score, \"ref:yyy\": score, ...}}\n'
            f"Utilise exactement les ref entre crochets. Aucun texte hors du JSON."
        )

        try:
            response = await llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            return self._parse_scores(content, candidates)
        except Exception as exc:
            logger.warning("LLM rescore batch failed: %s", exc)
            return {}

    @staticmethod
    def _parse_scores(raw: str, candidates: list[dict]) -> dict[str, float]:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return {}
        try:
            data = json.loads(raw[start:end])
        except json.JSONDecodeError:
            return {}

        valid_refs = {c["offer_ref"] for c in candidates}
        result: dict[str, float] = {}
        for key, value in data.items():
            ref = key[4:] if key.startswith("ref:") else key
            if ref in valid_refs:
                try:
                    result[ref] = max(0.0, min(10.0, float(value)))
                except (TypeError, ValueError):
                    pass
        return result


def llm_score_to_bonus(score: float) -> float:
    """Convertit un score LLM (0–10) en delta de points (−20 à +20)."""
    return (score - 5.0) * LLM_BONUS_SCALE
