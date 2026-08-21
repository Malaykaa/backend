"""Service career_references — référentiel curaté métiers/compétences/formations.

Mirroir volontaire de scraped_offer_service.py (search_for_agent/get_by_ref, préfixe
de référence, sérialisation restreinte) : même mécanisme déjà vérifié pour les offres,
appliqué ici à un référentiel bien plus petit et curaté à la main — pas d'embeddings,
un simple filtre pays + mots-clés suffit à ce volume (30-50 lignes).
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.career_reference import CareerReference

_CAREER_REF_PREFIX = "career:"

# Mots trop courts/génériques pour servir de filtre de recherche.
_STOPWORDS = {
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles", "le", "la", "les",
    "un", "une", "des", "de", "du", "et", "ou", "pour", "dans", "avec", "que", "qui",
    "est", "suis", "es", "sont", "mon", "ma", "mes", "ton", "ta", "tes",
}


def _serialize_for_agent(c: CareerReference) -> dict:
    return {
        "career_ref": f"{_CAREER_REF_PREFIX}{c.id}",
        "title": c.title,
        "category": c.category,
        "description": (c.description or "")[:500] or None,
        "key_skills": c.key_skills or [],
        "example_formations": c.example_formations or [],
    }


def _parse_career_ref(career_ref: str) -> uuid.UUID | None:
    if not career_ref or not career_ref.startswith(_CAREER_REF_PREFIX):
        return None
    try:
        return uuid.UUID(career_ref[len(_CAREER_REF_PREFIX):])
    except ValueError:
        return None


def _flatten_skills(value) -> str | None:
    """`Profile.skills` n'a jamais eu de forme unique selon l'étape d'inscription
    qui l'a écrit : liste de chaînes ou dict (`{"Python": "avancé"}`). On prend
    tout ce qui est exploitable des deux côtés (clés et valeurs pour un dict)
    plutôt que de parier sur une forme précise (même logique que
    `service_matching._flatten_json_field`, dupliquée ici pour ne pas coupler
    ce service à un autre)."""
    if isinstance(value, dict):
        parts = [str(v) for v in list(value.keys()) + list(value.values()) if v]
    elif isinstance(value, list):
        parts = [str(v) for v in value if v]
    else:
        return None
    return " ".join(parts) or None


def _keywords(*texts: str | None) -> list[str]:
    words: list[str] = []
    for text in texts:
        if not text:
            continue
        for word in text.lower().split():
            word = word.strip(".,;:!?()\"'")
            if len(word) >= 4 and word not in _STOPWORDS:
                words.append(word)
    return words


class CareerReferenceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def search_for_agent(
        self, *, profile: dict, message: str, limit: int = 5,
    ) -> list[dict]:
        """Recherche par pays + mots-clés — candidats, pas un jugement de pertinence
        (c'est l'agent qui choisit ensuite lesquels montrer, cf. app.agents.base).
        Retourne [] si la table est vide ou si aucun mot-clé n'est exploitable.
        """
        country = profile.get("country")
        stmt = select(CareerReference)
        if country:
            stmt = stmt.where(or_(CareerReference.country == country, CareerReference.country.is_(None)))

        interests = profile.get("interests")
        interests_text = " ".join(interests) if isinstance(interests, list) else None
        skills_text = _flatten_skills(profile.get("skills"))
        keywords = _keywords(
            message, profile.get("domain"), profile.get("field_of_study"),
            interests_text, skills_text,
        )
        if keywords:
            conditions = [
                or_(
                    CareerReference.title.ilike(f"%{kw}%"),
                    CareerReference.category.ilike(f"%{kw}%"),
                    CareerReference.description.ilike(f"%{kw}%"),
                )
                for kw in keywords
            ]
            stmt = stmt.where(or_(*conditions))

        stmt = stmt.limit(limit)
        results = list(self.db.execute(stmt).scalars().all())
        return [_serialize_for_agent(c) for c in results if c.title]

    def get_by_ref(self, career_ref: str) -> dict | None:
        career_id = _parse_career_ref(career_ref)
        if career_id is None:
            return None
        career = self.db.get(CareerReference, career_id)
        if not career or not career.title:
            return None
        return _serialize_for_agent(career)
