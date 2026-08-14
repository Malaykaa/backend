"""Matching demandes ↔ prestataires.

Ce module ne modifie AUCUN code existant. Il importe les primitives déjà
écrites et testées pour les offres scrapées, et les applique à de nouveaux
objets : les modèles exposent volontairement `title`, `normalized_title` et
`description`, les trois attributs que `_term_coverage` lit par typage canard.

Conséquence : le classement obéit aux mêmes règles que le matching d'offres
(échelle 0–100, fusion sémantique + lexicale, corroboration bornée), sans
duplication de logique ni risque de régression sur l'existant.

Deux viviers, jamais mélangés :
- `provider` : vitrines publiées, interrogées à la création de la demande ;
- `public`   : tous les utilisateurs, uniquement si le client élargit lui-même.
"""

from __future__ import annotations

import logging
import unicodedata
import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.service import (
    MatchDecision, MatchSource, ProviderStatus, ServiceProvider,
    ServiceRequest, ServiceRequestMatch,
)
from app.models.user import Profile, User
from app.models.user_intent import UserIntent
from app.services.embedding_service import get_embedding_service
# Primitives partagées avec le matching d'offres. Importées volontairement
# plutôt que réécrites : toute correction faite là-bas (pondération titre,
# frontières de mot, bonus de corroboration borné) vaut automatiquement ici.
from app.services.scraped_offer_service import (
    MATCH_MODE_LEXICAL,
    MATCH_MODE_SEMANTIC,
    _PROFILE_WEIGHT,
    _RELEVANCE_WEIGHT,
    _contains_word,
    _fuse_results,
    _term_coverage,
)

logger = logging.getLogger(__name__)

# Nombre de destinataires sollicités par vague. Sans plafond, une demande vague
# partirait à tout le monde et brûlerait le réseau dès la première semaine.
MAX_RECIPIENTS_PER_WAVE = 8

# Vivier interrogé avant scoring — large, car le filtrage géographique et le
# classement fin se font ensuite en Python.
_CANDIDATE_POOL = MAX_RECIPIENTS_PER_WAVE * 6

# Seuil sous lequel on ne sollicite personne : mieux vaut proposer au client
# d'élargir que de notifier des prestataires hors sujet, qui se désabonneront.
MIN_MATCH_SCORE = 25.0

# Pertinence minimale au besoin exprimé, indépendamment de tout bonus.
#
# Sans ce garde-fou, la proximité géographique seule suffisait à franchir
# MIN_MATCH_SCORE : un développeur web à Abidjan obtenait 33 points sur une
# demande de plomberie, uniquement parce qu'il était dans la bonne ville.
# La géographie doit départager des candidats pertinents, jamais en qualifier
# un qui ne l'est pas — même règle que le bonus de corroboration du matching
# d'offres, qui ne peut pas renverser un écart de pertinence réel.
MIN_RELEVANCE = 0.15


def _normalize_title(text: str) -> str:
    """Titre normalisé pour le matching — même traitement que les offres."""
    lowered = (text or "").strip().lower()
    nfkd = unicodedata.normalize("NFD", lowered)
    without_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", without_accents).strip()[:300]


def build_search_terms(*, title: str, description: str, keywords: list | None) -> list[str]:
    """Termes de recherche issus d'une demande ou d'une vitrine.

    Les mots-clés saisis librement priment, complétés par les mots du titre.
    La description n'est pas découpée : elle est trop bruitée pour produire des
    termes discriminants, et sert déjà via la couverture textuelle.
    """
    terms: list[str] = []
    for kw in (keywords or []):
        if kw and str(kw).strip():
            terms.append(str(kw).strip().lower())
    for word in _normalize_title(title).split():
        if len(word) > 2 and word not in terms:
            terms.append(word)
    return terms[:12]


def embedding_text(*, title: str, description: str, keywords: list | None,
                   city: str | None = None, country: str | None = None) -> str:
    """Texte vectorisé — titre en tête, comme pour les offres."""
    parts = [title or ""]
    if keywords:
        parts.append(" ".join(str(k) for k in keywords if k))
    if city:
        parts.append(city)
    if country:
        parts.append(country)
    if description:
        parts.append(description[:2000])
    return "\n".join(p for p in parts if p)


def _geo_bonus(*, request: ServiceRequest, city: str | None, country: str | None) -> float:
    """Proximité géographique, dans [0, 1].

    Volontairement tolérant : beaucoup de prestations se font à distance, et
    une ville non renseignée ne doit pas exclure un bon profil.
    """
    if request.city and city and _contains_word(city.lower(), request.city.lower()):
        return 1.0
    if request.country and country and country.strip().lower() == request.country.strip().lower():
        return 0.6
    return 0.0


class _Candidate:
    """Adaptateur minimal exposant les attributs attendus par `_term_coverage`."""

    __slots__ = ("title", "normalized_title", "description")

    def __init__(self, title: str | None, normalized_title: str | None, description: str | None):
        self.title = title
        self.normalized_title = normalized_title
        self.description = description


def _score(
    *, request: ServiceRequest, terms: list[str],
    title: str | None, normalized_title: str | None, description: str | None,
    city: str | None, country: str | None,
    cosine: float | None = None,
) -> float:
    """Score 0–100, même composition que le matching d'offres.

    Deux tiers pour la pertinence au besoin exprimé, un tiers pour le contexte
    (ici la proximité géographique). Conserver ce ratio garantit que les seuils
    et les pourcentages affichés restent comparables d'un module à l'autre.
    """
    relevance = (
        cosine if cosine is not None
        else _term_coverage(_Candidate(title, normalized_title, description), terms)
    )
    # Sans pertinence minimale au besoin, aucun bonus ne rattrape : un profil
    # hors sujet dans la bonne ville reste hors sujet.
    if relevance < MIN_RELEVANCE:
        return 0.0
    geo = _geo_bonus(request=request, city=city, country=country)
    return 100.0 * (_RELEVANCE_WEIGHT * relevance + _PROFILE_WEIGHT * geo)


class ServiceMatchingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Indexation ───────────────────────────────────────────────────────────

    async def index_request(self, request: ServiceRequest) -> None:
        request.normalized_title = _normalize_title(request.title) or None
        request.embedding = await self._embed(
            embedding_text(
                title=request.title, description=request.description,
                keywords=request.keywords, city=request.city, country=request.country,
            )
        )

    async def index_provider(self, provider: ServiceProvider) -> None:
        provider.normalized_title = _normalize_title(provider.title) or None
        provider.embedding = await self._embed(
            embedding_text(
                title=provider.title, description=provider.description,
                keywords=provider.keywords, city=provider.city, country=provider.country,
            )
        )

    async def _embed(self, text: str) -> list[float] | None:
        svc = get_embedding_service()
        if not svc.available or not text:
            return None
        try:
            return await svc.embed(text)
        except Exception:
            logger.warning("[ServiceMatching] échec d'embedding — repli lexical", exc_info=True)
            return None

    # ── Vivier 1 : les vitrines publiées ─────────────────────────────────────

    async def match_providers(
        self, request: ServiceRequest, *, limit: int = MAX_RECIPIENTS_PER_WAVE,
    ) -> list[dict]:
        """Prestataires publiés correspondant à la demande, classés.

        Le demandeur est exclu de ses propres résultats — un utilisateur peut
        parfaitement être prestataire et client, mais pas des deux côtés de la
        même demande.
        """
        terms = build_search_terms(
            title=request.title, description=request.description, keywords=request.keywords,
        )

        base = select(ServiceProvider).where(
            ServiceProvider.status == ProviderStatus.published,
            ServiceProvider.user_id != request.requester_id,
        )

        lexical: list[dict] = []
        for p in self.db.execute(base.limit(_CANDIDATE_POOL)).scalars().all():
            score = _score(
                request=request, terms=terms,
                title=p.title, normalized_title=p.normalized_title, description=p.description,
                city=p.city, country=p.country,
            )
            lexical.append(self._as_result(p, score, MATCH_MODE_LEXICAL))

        semantic: list[dict] = []
        if request.embedding is not None:
            distance = ServiceProvider.embedding.cosine_distance(request.embedding)
            rows = self.db.execute(
                base.where(ServiceProvider.embedding.isnot(None))
                .add_columns(distance.label("d"))
                .order_by(distance)
                .limit(_CANDIDATE_POOL)
            ).all()
            for p, d in rows:
                cosine = max(0.0, 1.0 - float(d))
                score = _score(
                    request=request, terms=terms,
                    title=p.title, normalized_title=p.normalized_title, description=p.description,
                    city=p.city, country=p.country, cosine=cosine,
                )
                semantic.append(self._as_result(p, score, MATCH_MODE_SEMANTIC))

        fused = _fuse_results(semantic, lexical, limit=limit * 2)
        retained = [r for r in fused if r["match_score"] >= MIN_MATCH_SCORE][:limit]
        logger.info(
            "[ServiceMatching] demande=%s : %d sém. + %d lex. → %d retenu(s)",
            request.id, len(semantic), len(lexical), len(retained),
        )
        return retained

    def _as_result(self, p: ServiceProvider, score: float, mode: str) -> dict:
        # `offer_ref` et `match_score` sont les clés attendues par `_fuse_results`.
        return {
            "offer_ref": f"provider:{p.id}",
            "provider_id": str(p.id),
            "user_id": str(p.user_id),
            "match_score": round(max(0.0, min(100.0, score)), 1),
            "match_mode": mode,
            "relevance_score": score,
        }

    # ── Vivier 2 : le grand public ───────────────────────────────────────────

    def match_public(
        self, request: ServiceRequest, *, limit: int = MAX_RECIPIENTS_PER_WAVE,
    ) -> list[dict]:
        """Utilisateurs dont les intentions déclarées recoupent la demande.

        Uniquement lexical : ces utilisateurs n'ont pas de vitrine vectorisée,
        et leurs intentions sont déjà des textes courts et ciblés. Leur profil
        n'est jamais montré au client à ce stade — seulement après acceptation.

        Les personnes déjà sollicitées au premier tour sont exclues, ce que la
        contrainte d'unicité (request_id, user_id) garantirait de toute façon.
        """
        terms = build_search_terms(
            title=request.title, description=request.description, keywords=request.keywords,
        )
        if not terms:
            return []

        already = {
            row[0] for row in self.db.execute(
                select(ServiceRequestMatch.user_id).where(
                    ServiceRequestMatch.request_id == request.id
                )
            ).all()
        }

        rows = self.db.execute(
            select(UserIntent, Profile)
            .outerjoin(Profile, Profile.user_id == UserIntent.user_id)
            .where(UserIntent.user_id != request.requester_id)
            .order_by(UserIntent.extracted_at.desc())
            .limit(_CANDIDATE_POOL * 3)
        ).all()

        best: dict[str, dict] = {}
        for intent, profile in rows:
            if intent.user_id in already:
                continue
            score = _score(
                request=request, terms=terms,
                title=intent.domain or "",
                normalized_title=None,
                description=" ".join(
                    filter(None, [intent.intent_summary, " ".join(intent.keywords or [])])
                ),
                city=profile.city if profile else None,
                country=profile.country if profile else None,
            )
            if score < MIN_MATCH_SCORE:
                continue
            key = str(intent.user_id)
            # Un utilisateur peut avoir plusieurs intentions : on ne retient que
            # la meilleure, sinon il occuperait plusieurs places de la vague.
            if key not in best or score > best[key]["match_score"]:
                best[key] = {
                    "offer_ref": f"user:{intent.user_id}",
                    "provider_id": None,
                    "user_id": key,
                    "match_score": round(max(0.0, min(100.0, score)), 1),
                    "match_mode": MATCH_MODE_LEXICAL,
                    "relevance_score": score,
                }

        ranked = sorted(best.values(), key=lambda r: r["match_score"], reverse=True)
        logger.info(
            "[ServiceMatching] demande=%s grand public : %d candidat(s) → %d retenu(s)",
            request.id, len(best), min(len(ranked), limit),
        )
        return ranked[:limit]

    # ── Création des sollicitations ──────────────────────────────────────────

    def create_matches(
        self, request: ServiceRequest, results: list[dict], source: MatchSource,
    ) -> list[ServiceRequestMatch]:
        """Matérialise les rapprochements retenus, sans jamais dupliquer.

        Retourne uniquement les lignes réellement créées, pour que l'appelant
        sache qui notifier.
        """
        existing = {
            str(row[0]) for row in self.db.execute(
                select(ServiceRequestMatch.user_id).where(
                    ServiceRequestMatch.request_id == request.id
                )
            ).all()
        }

        created: list[ServiceRequestMatch] = []
        for r in results:
            if r["user_id"] in existing:
                continue
            match = ServiceRequestMatch(
                request_id=request.id,
                user_id=r["user_id"],
                provider_id=r["provider_id"],
                source=source,
                decision=MatchDecision.pending,
                match_score=r["match_score"],
                match_mode=r["match_mode"],
                notified_at=datetime.now(timezone.utc),
            )
            self.db.add(match)
            created.append(match)
            existing.add(r["user_id"])

        self.db.flush()
        return created
