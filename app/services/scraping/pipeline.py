"""Post-scrape pipeline — nettoyage, normalisation, scoring qualité, classification.

Fonctions pures + une méthode DB pour appliquer le pipeline sur une offre en base.
Inclut désormais l'indexation sémantique via EmbeddingService (en batch async).
Porté de post-scrape-pipeline.service.ts (NestJS).
"""

from __future__ import annotations

import logging
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.services.embedding_service import get_embedding_service

logger = logging.getLogger(__name__)

# ── Fonctions pures ────────────────────────────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    """Nettoie une chaîne : supprime HTML, espaces multiples, trim."""
    if not value:
        return ""
    text = _HTML_TAG_RE.sub(" ", value)
    text = text.replace("\u00a0", " ")
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


def normalize_title(title: str) -> str:
    """Titre normalisé pour déduplication (minuscules, sans accents, tronqué)."""
    cleaned = clean_text(title).lower()
    # Supprimer les diacritiques
    nfkd = unicodedata.normalize("NFD", cleaned)
    without_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return _MULTI_SPACE_RE.sub(" ", without_accents).strip()[:300]


def compute_quality_score(
    *,
    title: str,
    description: str | None = None,
    url: str | None = None,
    location: str | None = None,
    company: str | None = None,
    posted_at: object | None = None,
    expires_at: object | None = None,
) -> float:
    """Score qualité 0-100 : complétude, fraîcheur, clarté."""
    score = 0.0
    if len(clean_text(title)) >= 5:
        score += 25
    desc_len = len(clean_text(description))
    if desc_len >= 50:
        score += 25
    elif desc_len >= 20:
        score += 15
    if url and url.strip():
        score += 15
    if (location and location.strip()) or (company and company.strip()):
        score += 10
    if posted_at is not None:
        score += 10
    if expires_at is not None:
        score += 5
    return min(100.0, score)


# Mots-clés de classification par type d'offre
_CLASSIFY_KEYWORDS: list[tuple[ScrapedOfferType, list[str]]] = [
    (ScrapedOfferType.job, [
        "emploi", "recrutement", "cdi", "cdd", "stage", "alternance", "h/f", "poste",
        "hiring", "vacancy", "intern",
    ]),
    (ScrapedOfferType.formation, [
        "formation", "certification", "mooc", "cours", "diplôme", "apprentissage",
        "training", "course", "workshop",
    ]),
    (ScrapedOfferType.grant, [
        "subvention", "financement", "aide ", "bourse", "appel à projet",
        "grant", "funding",
    ]),
    (ScrapedOfferType.partnership, [
        "partenariat", "collaboration", "alliance", "partnership",
    ]),
    (ScrapedOfferType.call_for_applications, [
        "appel à candidature", "candidater", "date limite",
        "call for applications", "deadline", "apply now",
    ]),
    (ScrapedOfferType.opportunity, [
        "opportunité", "incubateur", "accélérateur", "concours",
        "accelerator", "incubator", "competition",
    ]),
    (ScrapedOfferType.resource, [
        "ressource", "outil", "guide", "template", "toolkit",
    ]),
]


def classify_offer(
    title: str,
    description: str | None = None,
    current_type: ScrapedOfferType | None = None,
) -> ScrapedOfferType:
    """Classification par mots-clés pour affiner le type d'offre."""
    text = f"{clean_text(title)} {clean_text(description)}".lower()
    for offer_type, keywords in _CLASSIFY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return offer_type
    return current_type or ScrapedOfferType.opportunity


# ── Application du pipeline sur une offre en DB ──────────────────────────────


def process_offer(db: Session, offer_id: object) -> None:
    """Applique nettoyage + scoring + normalisation sur une offre existante.

    Ne génère PAS l'embedding (appel HTTP async) — utiliser embed_pending()
    après cette étape pour vectoriser le batch fraîchement ingéré.
    """
    offer = db.get(ScrapedOffer, offer_id)
    if not offer:
        return

    offer.title = clean_text(offer.title) or offer.title
    if offer.description:
        offer.description = clean_text(offer.description)
    offer.normalized_title = normalize_title(offer.title) or None
    offer.quality_score = compute_quality_score(
        title=offer.title,
        description=offer.description,
        url=offer.url,
        location=offer.location,
        company=offer.company,
        posted_at=offer.posted_at,
        expires_at=offer.expires_at,
    )
    db.flush()


# ── Indexation sémantique ────────────────────────────────────────────────────


def offer_text_for_embedding(offer: ScrapedOffer) -> str:
    """Concatène les champs textuels d'une offre pour l'embedding.

    Titre prioritaire (poids implicite via la position). On limite la
    description à 2000 chars pour éviter les longs payloads vers le provider.
    """
    parts = [offer.title or ""]
    if offer.company:
        parts.append(offer.company)
    if offer.location:
        parts.append(offer.location)
    if offer.description:
        parts.append(offer.description[:2000])
    return "\n".join(p for p in parts if p)


async def embed_pending(
    db: Session, *, limit: int | None = None,
) -> int:
    """Génère un embedding pour les offres actives avec embedding NULL.

    Retourne le nombre d'offres effectivement vectorisées. No-op silencieux
    si EmbeddingService n'est pas disponible (PERPLEXITY_API_KEY absent).
    Utilisé en fin de run_actor() et par scripts/backfill_embeddings.py.
    """
    svc = get_embedding_service()
    if not svc.available:
        return 0

    stmt = (
        select(ScrapedOffer)
        .where(ScrapedOffer.embedding.is_(None), ScrapedOffer.is_active.is_(True))
        .order_by(ScrapedOffer.scraped_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    offers = list(db.execute(stmt).scalars().all())
    if not offers:
        return 0

    texts = [offer_text_for_embedding(o) for o in offers]
    vectors = await svc.embed_batch(texts)

    indexed = 0
    for offer, vec in zip(offers, vectors):
        if vec is None:
            continue
        offer.embedding = vec
        indexed += 1
    db.flush()
    if indexed:
        logger.info(
            "[Embeddings] indexed %d/%d offers (limit=%s)",
            indexed, len(offers), limit,
        )
    return indexed
