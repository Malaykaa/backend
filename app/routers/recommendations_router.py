"""Router recommandations — scoring enrichi (fraîcheur + niveau + feedback + LLM).

GET  /recommendations/propositions      → liste scorée personnalisée
POST /recommendations/{offer_ref}/feedback → enregistre une action utilisateur
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.llm import get_llm_provider
from app.models.user import User
from app.models.user_offer_feedback import FEEDBACK_SCORE_DELTA, FeedbackAction
from app.repositories.feedback_repo import FeedbackRepository
from app.repositories.intent_repo import IntentRepository
from app.services.llm_rescorer import LLMRescorer, llm_score_to_bonus
from app.services.scraped_offer_service import ScrapedOfferService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

# Nombre de recommandations retournées par défaut
DEFAULT_LIMIT = 20
# Âge max (jours) pour le calcul de fraîcheur : au-delà → pénalité max
FRESHNESS_MAX_AGE_DAYS = 60


# ── Schemas ────────────────────────────────────────────────────────────────────

class RecommendationItem(BaseModel):
    offer_ref: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str | None = None
    type: str | None = None
    score: float
    freshness_factor: float = 1.0
    feedback: str | None = None
    source: str = "scraped"


class FeedbackRequest(BaseModel):
    action: str  # clicked | saved | applied | ignored | dismissed


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/propositions", response_model=list[RecommendationItem])
async def get_propositions(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = DEFAULT_LIMIT,
) -> list[RecommendationItem]:
    """Retourne les recommandations personnalisées pour l'utilisateur connecté.

    Pipeline :
    1. Charge les intentions extraites du user
    2. Recherche des offres scrapées matchant la meilleure intention
    3. Applique les 4 facteurs de précision (fraîcheur, niveau, feedback, LLM)
    4. Trie par score final et retourne les top N
    """
    user_id: uuid.UUID = current_user.id

    intent_repo = IntentRepository(db)
    feedback_repo = FeedbackRepository(db)
    offer_service = ScrapedOfferService(db)

    # Charger les intentions
    intents = intent_repo.get_latest_for_user(user_id, limit=5)
    if not intents:
        # Cold start : aucune UserIntent disponible (< 8 messages dans un thread).
        # Recommandations de bienvenue basées sur le profil d'inscription.
        return _cold_start_recommendations(current_user, db, limit)

    # L'intention principale reste intents[0] (la plus récente) pour le
    # rescoring LLM et le cache — elle sert de référence de qualité.
    primary_intent = intents[0]

    # Dédupliquer par intent_type : 1 intention par type (la plus récente).
    # Évite de faire plusieurs searches sémantiques pour le même domaine.
    unique_intents = _deduplicate_by_type(intents)

    # Rechercher les offres pour chaque type d'intention en parallèle.
    # asyncio.gather préserve l'ordre et isole les exceptions par intent.
    search_results = await asyncio.gather(
        *[offer_service.search_for_matching(intent, limit=limit * 2)
          for intent in unique_intents],
        return_exceptions=True,
    )

    # Fusionner les résultats : par offer_ref, garder le meilleur score
    # et l'intention ayant produit le match (pour le facteur niveau).
    merged: dict[str, dict] = {}
    for intent, result in zip(unique_intents, search_results):
        if isinstance(result, Exception):
            logger.warning("search_for_matching failed for intent %s: %s", intent.intent_type, result)
            continue
        for offer in result:
            ref = (
                f"scraped:{offer.get('id', '')}"
                if "id" in offer
                else offer.get("offer_ref", "")
            )
            if not ref or ref == "scraped:":
                continue
            existing = merged.get(ref)
            score = offer.get("relevance_score", 0.0)
            if existing is None or score > existing["relevance_score"]:
                merged[ref] = {**offer, "_matched_intent": intent, "relevance_score": score}

    raw_offers = list(merged.values())
    if not raw_offers:
        return []

    # Charger le feedback utilisateur (dict[offer_ref → action])
    user_feedbacks = feedback_repo.get_user_feedbacks(user_id)

    # Construire les candidats avec scores heuristiques enrichis
    candidates: list[dict] = []
    for offer in raw_offers:
        offer_ref = (
            f"scraped:{offer.get('id', '')}"
            if "id" in offer
            else offer.get("offer_ref", "")
        )
        if not offer_ref or offer_ref == "scraped:":
            continue

        base_score = offer.get("relevance_score", 0.0)
        matched_intent = offer.get("_matched_intent", primary_intent)

        # ── Facteur 1 : Fraîcheur ────────────────────────────────────────────
        scraped_at: datetime | None = offer.get("scraped_at")
        freshness = _freshness_factor(scraped_at)
        base_score *= freshness

        # ── Facteur 2 : Matching niveau (par rapport à l'intention du match) ──
        if matched_intent.level:
            offer_text = f"{offer.get('title','')} {offer.get('description','')}".lower()
            if matched_intent.level.lower() in offer_text:
                base_score += 8.0

        # ── Facteur 3 : Feedback utilisateur ─────────────────────────────────
        action = user_feedbacks.get(offer_ref)
        if action == FeedbackAction.applied:
            continue  # déjà postulé → filtrer complètement
        if action:
            delta = FEEDBACK_SCORE_DELTA.get(action, 0.0)
            base_score = max(0.0, base_score + delta)

        candidates.append({
            "offer_ref": offer_ref,
            "title": offer.get("title", ""),
            "company": offer.get("company"),
            "location": offer.get("location"),
            "url": offer.get("url"),
            "type": offer.get("type"),
            "description": offer.get("description", ""),
            "base_score": base_score,
            "freshness_factor": freshness,
            "feedback": action.value if action else None,
        })

    if not candidates:
        return []

    # ── Facteur 4 : Scores LLM en cache ──────────────────────────────────────
    # Le cache LLM utilise primary_intent comme référence de version.
    llm_scores: dict[str, float] = {}
    rescorer = LLMRescorer(db)
    try:
        for c in candidates[:12]:
            cached = feedback_repo.get_llm_score(
                user_id=user_id,
                offer_ref=c["offer_ref"],
                intent_version=primary_intent.version,
            )
            if cached is not None:
                llm_scores[c["offer_ref"]] = cached
    except Exception as exc:
        logger.warning("Lecture cache LLM scores: %s", exc)

    # Déclencher un rescoring LLM en arrière-plan si des cache-miss existent
    uncached_refs = {c["offer_ref"] for c in candidates[:12]} - set(llm_scores)
    if uncached_refs:
        background_tasks.add_task(
            _background_rescore, db, user_id, candidates, primary_intent
        )

    # Appliquer le bonus LLM aux candidats déjà en cache
    for c in candidates:
        llm_score = llm_scores.get(c["offer_ref"])
        if llm_score is not None:
            c["base_score"] = max(0.0, c["base_score"] + llm_score_to_bonus(llm_score))

    # Trier et tronquer
    candidates.sort(key=lambda c: c["base_score"], reverse=True)
    top = candidates[:limit]

    return [
        RecommendationItem(
            offer_ref=c["offer_ref"],
            title=c["title"],
            company=c.get("company"),
            location=c.get("location"),
            url=c.get("url"),
            type=c.get("type"),
            score=round(c["base_score"], 2),
            freshness_factor=round(c["freshness_factor"], 3),
            feedback=c.get("feedback"),
            source="scraped",
        )
        for c in top
    ]


@router.post("/{offer_ref:path}/feedback", status_code=status.HTTP_204_NO_CONTENT)
def record_feedback(
    offer_ref: str,
    body: FeedbackRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Enregistre une interaction utilisateur sur une recommandation.

    Actions valides : clicked, saved, applied, ignored, dismissed.
    """
    try:
        action = FeedbackAction(body.action)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Action invalide : {body.action}")

    if action == FeedbackAction.llm_validated:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Action réservée au système.")

    repo = FeedbackRepository(db)
    repo.upsert_feedback(
        user_id=current_user.id,
        offer_ref=offer_ref,
        action=action,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _deduplicate_by_type(intents: list) -> list:
    """Retourne une liste d'intentions dédupliquées par intent_type.

    Pour chaque type, garde la plus récente (intents est trié par extracted_at desc).
    Objectif : éviter des recherches redondantes quand l'utilisateur a plusieurs threads
    du même type (ex: deux threads "career") et s'assurer que chaque type distinct
    génère ses propres recommandations.
    """
    seen: set[str] = set()
    unique: list = []
    for intent in intents:
        key = intent.intent_type or "unknown"
        if key not in seen:
            seen.add(key)
            unique.append(intent)
    return unique


def _cold_start_recommendations(
    user,
    db: Session,
    limit: int,
) -> list[RecommendationItem]:
    """Recommandations de bienvenue quand aucun UserIntent n'est encore disponible.

    Utilise primary_role + domain/field_of_study + country du profil d'inscription
    pour afficher des offres pertinentes dès la création du compte, sans attendre
    que l'utilisateur ait suffisamment chatté (EXTRACTION_THRESHOLD = 8 messages).

    Le scoring par intent extrait (search_for_matching) prend le relais dès qu'une
    UserIntent est disponible — ces deux chemins sont mutuellement exclusifs.
    """
    profile = user.profile
    if not profile:
        return []

    # Mots-clés domaine/filière déclarés à l'inscription
    domain_keywords: list[str] = []
    if profile.domain:
        domain_keywords.append(profile.domain)
    if profile.field_of_study:
        domain_keywords.append(profile.field_of_study)
    if profile.current_status:
        domain_keywords.append(profile.current_status)

    offer_svc = ScrapedOfferService(db)
    raw_offers = offer_svc.search_for_profile(
        primary_role=profile.primary_role,
        domain_keywords=domain_keywords,
        country=profile.country,
        limit=limit,
    )

    return [
        RecommendationItem(
            offer_ref=o["offer_ref"],
            title=o["title"],
            company=o.get("company"),
            location=o.get("location"),
            url=o.get("url"),
            type=o.get("type"),
            score=round(o.get("relevance_score", 0.5), 2),
            source="scraped",
        )
        for o in raw_offers
        if o.get("title")
    ]


def _freshness_factor(scraped_at: datetime | None) -> float:
    """Facteur multiplicatif basé sur l'âge de l'offre (0.4 à 1.0)."""
    if scraped_at is None:
        return 0.8  # info manquante → facteur neutre-légèrement pénalisé

    if scraped_at.tzinfo is None:
        scraped_at = scraped_at.replace(tzinfo=timezone.utc)

    age_days = (datetime.now(timezone.utc) - scraped_at).days
    if age_days <= 7:
        return 1.0
    if age_days >= FRESHNESS_MAX_AGE_DAYS:
        return 0.4
    # Décroissance linéaire entre 7 et 60 jours
    return 1.0 - (age_days - 7) / (FRESHNESS_MAX_AGE_DAYS - 7) * 0.6


async def _background_rescore(
    db: Session,
    user_id: uuid.UUID,
    candidates: list[dict],
    intent,
) -> None:
    """Tâche de fond : appelle le LLM pour rescorer les candidats non cachés."""
    try:
        llm = get_llm_provider()
        rescorer = LLMRescorer(db)
        await rescorer.rescore_batch(
            user_id=user_id,
            candidates=candidates,
            intent=intent,
            llm=llm,
        )
        db.commit()
    except Exception as exc:
        logger.warning("Background LLM rescore failed: %s", exc)
