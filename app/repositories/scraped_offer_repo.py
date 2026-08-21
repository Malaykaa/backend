"""Repository scraped_offers — recherche d'offres pour enrichir le contexte agent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Row, select, func, or_, case, extract
from sqlalchemy.orm import Session

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.repositories.base import BaseRepository


# Mapping intent agent → types d'offres pertinents
#
# "orientation", "freelance" et "coursework" manquaient ici alors que
# ChatService._enrich_with_offers ne les excluait déjà pas (seuls
# document/free le sont) : le lookup ci-dessous retournait donc toujours []
# pour ces trois agents (get() sur clé absente → None → search_for_agent
# renvoie [] immédiatement) — bug silencieux, aucune erreur ne le signalait.
_INTENT_OFFER_TYPES: dict[str, list[ScrapedOfferType]] = {
    "career": [ScrapedOfferType.job, ScrapedOfferType.formation, ScrapedOfferType.opportunity],
    "scholarship": [ScrapedOfferType.scholarship, ScrapedOfferType.grant, ScrapedOfferType.formation],
    "funding": [ScrapedOfferType.grant, ScrapedOfferType.partnership, ScrapedOfferType.call_for_applications],
    "tender": [ScrapedOfferType.call_for_applications, ScrapedOfferType.partnership],
    "study_grant": [ScrapedOfferType.scholarship, ScrapedOfferType.formation, ScrapedOfferType.opportunity],
    "exam": [ScrapedOfferType.formation, ScrapedOfferType.resource],
    "orientation": [ScrapedOfferType.job, ScrapedOfferType.formation, ScrapedOfferType.opportunity],
    "freelance": [ScrapedOfferType.job, ScrapedOfferType.opportunity],
    "coursework": [ScrapedOfferType.formation, ScrapedOfferType.resource],
}


def _safe_offer_type(value: str) -> ScrapedOfferType | None:
    """Convertit une string en ScrapedOfferType, None si valeur inconnue."""
    try:
        return ScrapedOfferType(value)
    except ValueError:
        return None

# ── Fraîcheur dynamique par type d'offre ─────────────────────────────────────
# expires_at n'est quasiment jamais rempli par les scrapers (seul Indeed essaie,
# rarement avec succès) — la fenêtre de fraîcheur scraped_at était donc le seul
# vrai garde-fou contre les offres expirées. Une fenêtre unique de 60 jours est
# bien trop longue pour des bourses/financements/appels à candidature dont la
# deadline réelle tombe souvent sous 3 semaines. Différenciée par type :
# court pour les offres à deadline serrée, plus large pour emploi/formation.
_OFFER_TYPE_MAX_AGE_DAYS: dict[ScrapedOfferType, int] = {
    ScrapedOfferType.scholarship:           21,
    ScrapedOfferType.grant:                 21,
    ScrapedOfferType.call_for_applications: 21,
    ScrapedOfferType.job:                   45,
    ScrapedOfferType.opportunity:           45,
    ScrapedOfferType.partnership:           45,
    ScrapedOfferType.formation:             60,
    ScrapedOfferType.resource:              60,
}
_DEFAULT_MAX_AGE_DAYS = 60


def _freshness_filter():
    """Filtre SQL : âge réel (now() - scraped_at) en jours <= seuil du type de la ligne.

    Évalué par ligne via CASE Postgres — fonctionne aussi bien pour une requête
    multi-types (search_for_agent) qu'un filtre sur un seul type ou aucun filtre.
    """
    max_age_case = case(
        *[(ScrapedOffer.offer_type == ot, days) for ot, days in _OFFER_TYPE_MAX_AGE_DAYS.items()],
        else_=_DEFAULT_MAX_AGE_DAYS,
    )
    age_in_days = extract("epoch", func.now() - ScrapedOffer.scraped_at) / 86400.0
    return age_in_days <= max_age_case


class ScrapedOfferRepository(BaseRepository[ScrapedOffer]):
    def __init__(self, db: Session) -> None:
        super().__init__(ScrapedOffer, db)

    def search_for_agent(
        self,
        intent: str,
        keywords: list[str] | None = None,
        country: str | None = None,
        limit: int = 5,
    ) -> list[ScrapedOffer]:
        """Recherche les offres pertinentes pour un agent donné.

        Filtrage : type d'offre (via intent), activité, fraîcheur (dynamique
        par type, cf. _freshness_filter), localisation.
        Tri : quality_score DESC, scraped_at DESC.
        """
        offer_types = _INTENT_OFFER_TYPES.get(intent)
        if not offer_types:
            return []

        stmt = (
            select(ScrapedOffer)
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.offer_type.in_(offer_types),
                _freshness_filter(),
                # Exclure les offres expirées
                or_(
                    ScrapedOffer.expires_at.is_(None),
                    ScrapedOffer.expires_at > datetime.now(timezone.utc),
                ),
            )
        )

        # Filtre par pays si disponible
        if country:
            country_lower = country.lower()
            stmt = stmt.where(
                or_(
                    ScrapedOffer.location.is_(None),
                    func.lower(ScrapedOffer.location).contains(country_lower),
                    func.lower(ScrapedOffer.location).in_(["africa", "afrique", "international", "global", "remote"]),
                )
            )

        # Tri par qualité puis fraîcheur
        stmt = stmt.order_by(
            ScrapedOffer.quality_score.desc().nulls_last(),
            ScrapedOffer.scraped_at.desc(),
        )

        # Déduplication par normalized_title : on récupère plus et on filtre
        stmt = stmt.limit(limit * 3)
        raw_results = list(self.db.execute(stmt).scalars().all())

        # Dédup en mémoire par normalized_title
        seen_titles: set[str] = set()
        deduped: list[ScrapedOffer] = []
        for offer in raw_results:
            key = offer.normalized_title or offer.title.lower()[:100]
            if key not in seen_titles:
                seen_titles.add(key)
                deduped.append(offer)
            if len(deduped) >= limit:
                break

        return deduped

    def search_by_keywords(
        self,
        terms: list[str],
        country: str | None = None,
        offer_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[ScrapedOffer]:
        """Recherche par mots-clés libres pour le matching d'intentions extraites.

        Différent de search_for_agent : pas de mapping par intent, recherche
        full-text sur title + description via ILIKE PostgreSQL.
        Fraîcheur dynamique par type (cf. _freshness_filter).
        """
        stmt = select(ScrapedOffer).where(
            ScrapedOffer.is_active.is_(True),
            _freshness_filter(),
            or_(
                ScrapedOffer.expires_at.is_(None),
                ScrapedOffer.expires_at > datetime.now(timezone.utc),
            ),
        )

        # Filtre par type(s) d'offre si fourni
        if offer_types:
            valid_types = [t for t in (_safe_offer_type(ot) for ot in offer_types) if t]
            if valid_types:
                stmt = stmt.where(ScrapedOffer.offer_type.in_(valid_types))

        # Filtre full-text par termes (OR entre termes, AND implicite via chaque filtre)
        if terms:
            keyword_filters = []
            for term in terms[:6]:  # limiter à 6 termes pour la perf
                t = f"%{term.lower()}%"
                keyword_filters.append(
                    or_(
                        func.lower(ScrapedOffer.title).like(t),
                        func.lower(ScrapedOffer.description).like(t),
                        func.lower(ScrapedOffer.normalized_title).like(t),
                    )
                )
            # Au moins un terme doit matcher
            stmt = stmt.where(or_(*keyword_filters))

        # Filtre géographique
        if country:
            c = country.lower()
            stmt = stmt.where(
                or_(
                    ScrapedOffer.location.is_(None),
                    func.lower(ScrapedOffer.location).contains(c),
                    func.lower(ScrapedOffer.location).in_(
                        ["africa", "afrique", "international", "global", "remote"]
                    ),
                )
            )

        stmt = stmt.order_by(
            ScrapedOffer.quality_score.desc().nulls_last(),
            ScrapedOffer.scraped_at.desc(),
        ).limit(limit)

        return list(self.db.execute(stmt).scalars().all())

    def search_by_embedding(
        self,
        query_vec: list[float],
        country: str | None = None,
        offer_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[tuple[ScrapedOffer, float]]:
        """Recherche sémantique via pgvector — cosine distance.

        Retourne les couples (offer, cosine_distance) triés par distance
        croissante (= similarité décroissante). Filtre uniquement les offres
        qui ont un embedding non-NULL (les autres ne peuvent pas être ranked
        par similarité ; le caller utilise alors search_by_keywords en
        fallback). Filtre aussi sur is_active, fraîcheur (dynamique par type,
        cf. _freshness_filter), expires_at, country, offer_types — symétrique
        à search_by_keywords.

        L'index HNSW (vector_cosine_ops) sur scraped_offers.embedding est
        utilisé automatiquement par PostgreSQL pour le tri.
        """
        if not query_vec:
            return []

        distance = ScrapedOffer.embedding.cosine_distance(query_vec).label("distance")

        stmt = select(ScrapedOffer, distance).where(
            ScrapedOffer.embedding.is_not(None),
            ScrapedOffer.is_active.is_(True),
            _freshness_filter(),
            or_(
                ScrapedOffer.expires_at.is_(None),
                ScrapedOffer.expires_at > datetime.now(timezone.utc),
            ),
        )

        if offer_types:
            valid_types = [t for t in (_safe_offer_type(ot) for ot in offer_types) if t]
            if valid_types:
                stmt = stmt.where(ScrapedOffer.offer_type.in_(valid_types))

        if country:
            c = country.lower()
            stmt = stmt.where(
                or_(
                    ScrapedOffer.location.is_(None),
                    func.lower(ScrapedOffer.location).contains(c),
                    func.lower(ScrapedOffer.location).in_(
                        ["africa", "afrique", "international", "global", "remote"]
                    ),
                )
            )

        stmt = stmt.order_by(distance).limit(limit)
        rows: list[Row] = list(self.db.execute(stmt).all())
        return [(row[0], float(row[1])) for row in rows]

    def count_with_embedding(self) -> int:
        """Nombre d'offres actives indexées (embedding non-NULL).

        Utilisé pour décider d'activer la recherche sémantique : si peu
        d'offres sont indexées, le fallback ILIKE reste préférable.
        """
        return int(
            self.db.execute(
                select(func.count(ScrapedOffer.id)).where(
                    ScrapedOffer.embedding.is_not(None),
                    ScrapedOffer.is_active.is_(True),
                )
            ).scalar_one()
        )

    def browse(
        self,
        offer_types: list[ScrapedOfferType] | None = None,
        country: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 50,
        max_age_days: int = 60,
    ) -> list[ScrapedOffer]:
        """Recherche filtrée pour la page Parcourir (liens depuis Tendances).

        Filtre par type(s) d'offre, pays et/ou mots-clés libres (skill).
        Tri : quality_score DESC, scraped_at DESC.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        stmt = select(ScrapedOffer).where(
            ScrapedOffer.is_active.is_(True),
            ScrapedOffer.scraped_at >= cutoff,
            or_(
                ScrapedOffer.expires_at.is_(None),
                ScrapedOffer.expires_at > datetime.now(timezone.utc),
            ),
        )
        if offer_types:
            stmt = stmt.where(ScrapedOffer.offer_type.in_(offer_types))
        if country:
            c = country.lower()
            stmt = stmt.where(
                or_(
                    func.lower(ScrapedOffer.location).contains(c),
                    func.lower(ScrapedOffer.location).in_(
                        ["africa", "afrique", "international", "global", "remote"]
                    ),
                )
            )
        if keywords:
            kw_filters = []
            for term in keywords[:4]:
                t = f"%{term.lower()}%"
                kw_filters.append(
                    or_(
                        func.lower(ScrapedOffer.title).like(t),
                        func.lower(ScrapedOffer.description).like(t),
                    )
                )
            stmt = stmt.where(or_(*kw_filters))
        stmt = stmt.order_by(
            ScrapedOffer.quality_score.desc().nulls_last(),
            ScrapedOffer.scraped_at.desc(),
        ).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def get_stats(self) -> dict[str, int]:
        """Stats rapides pour l'admin."""
        total = self.db.execute(
            select(func.count(ScrapedOffer.id)).where(ScrapedOffer.is_active.is_(True))
        ).scalar_one()

        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        recent = self.db.execute(
            select(func.count(ScrapedOffer.id)).where(ScrapedOffer.scraped_at >= cutoff)
        ).scalar_one()

        return {"total_active": total, "last_24h": recent}
