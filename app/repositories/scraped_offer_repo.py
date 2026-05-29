"""Repository scraped_offers — recherche d'offres pour enrichir le contexte agent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Row, select, func, or_
from sqlalchemy.orm import Session

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.repositories.base import BaseRepository


# Mapping intent agent → types d'offres pertinents
_INTENT_OFFER_TYPES: dict[str, list[ScrapedOfferType]] = {
    "career": [ScrapedOfferType.job, ScrapedOfferType.formation, ScrapedOfferType.opportunity],
    "scholarship": [ScrapedOfferType.scholarship, ScrapedOfferType.grant, ScrapedOfferType.formation],
    "funding": [ScrapedOfferType.grant, ScrapedOfferType.partnership, ScrapedOfferType.call_for_applications],
    "tender": [ScrapedOfferType.call_for_applications, ScrapedOfferType.partnership],
    "study_grant": [ScrapedOfferType.scholarship, ScrapedOfferType.formation, ScrapedOfferType.opportunity],
    "exam": [ScrapedOfferType.formation, ScrapedOfferType.resource],
}


class ScrapedOfferRepository(BaseRepository[ScrapedOffer]):
    def __init__(self, db: Session) -> None:
        super().__init__(ScrapedOffer, db)

    def search_for_agent(
        self,
        intent: str,
        keywords: list[str] | None = None,
        country: str | None = None,
        limit: int = 5,
        max_age_days: int = 30,
    ) -> list[ScrapedOffer]:
        """Recherche les offres pertinentes pour un agent donné.

        Filtrage : type d'offre (via intent), activité, fraîcheur, localisation.
        Tri : quality_score DESC, scraped_at DESC.
        """
        offer_types = _INTENT_OFFER_TYPES.get(intent)
        if not offer_types:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

        stmt = (
            select(ScrapedOffer)
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.offer_type.in_(offer_types),
                ScrapedOffer.scraped_at >= cutoff,
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
        offer_type: str | None = None,
        limit: int = 20,
        max_age_days: int = 60,
    ) -> list[ScrapedOffer]:
        """Recherche par mots-clés libres pour le matching d'intentions extraites.

        Différent de search_for_agent : pas de mapping par intent, recherche
        full-text sur title + description via ILIKE PostgreSQL.
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

        # Filtre par type d'offre si fourni
        if offer_type:
            try:
                ot = ScrapedOfferType(offer_type)
                stmt = stmt.where(ScrapedOffer.offer_type == ot)
            except ValueError:
                pass  # type inconnu → ignorer le filtre

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
        offer_type: str | None = None,
        limit: int = 20,
        max_age_days: int = 60,
    ) -> list[tuple[ScrapedOffer, float]]:
        """Recherche sémantique via pgvector — cosine distance.

        Retourne les couples (offer, cosine_distance) triés par distance
        croissante (= similarité décroissante). Filtre uniquement les offres
        qui ont un embedding non-NULL (les autres ne peuvent pas être ranked
        par similarité ; le caller utilise alors search_by_keywords en
        fallback). Filtre aussi sur is_active, fraîcheur, expires_at, country,
        offer_type — symétrique à search_by_keywords.

        L'index HNSW (vector_cosine_ops) sur scraped_offers.embedding est
        utilisé automatiquement par PostgreSQL pour le tri.
        """
        if not query_vec:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        distance = ScrapedOffer.embedding.cosine_distance(query_vec).label("distance")

        stmt = select(ScrapedOffer, distance).where(
            ScrapedOffer.embedding.is_not(None),
            ScrapedOffer.is_active.is_(True),
            ScrapedOffer.scraped_at >= cutoff,
            or_(
                ScrapedOffer.expires_at.is_(None),
                ScrapedOffer.expires_at > datetime.now(timezone.utc),
            ),
        )

        if offer_type:
            try:
                ot = ScrapedOfferType(offer_type)
                stmt = stmt.where(ScrapedOffer.offer_type == ot)
            except ValueError:
                pass

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
