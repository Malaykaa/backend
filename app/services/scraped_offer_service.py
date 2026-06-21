"""Service scraped_offers — recherche d'offres pertinentes pour enrichir le contexte agent.

Deux modes :
- search_for_agent()   : recherche classique par intent + country (contexte chat)
- search_for_matching(): recherche sémantique pgvector (cosine) + scoring profil,
                         avec fallback ILIKE si EmbeddingService indisponible
                         ou si aucune offre n'est encore indexée.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.repositories.scraped_offer_repo import ScrapedOfferRepository
from app.services.embedding_service import get_embedding_service

if TYPE_CHECKING:
    from app.models.scraped_offer import ScrapedOffer
    from app.models.user_intent import UserIntent

logger = logging.getLogger(__name__)

# Intents pour lesquels on ne cherche PAS d'offres dans le contexte chat
_SKIP_INTENTS = {"document", "free"}

# Mapping primary_role (profil d'inscription) → clé intent pour le cold start.
# Utilise les mêmes clés que _INTENT_OFFER_TYPES dans le repo (pas de duplication).
# Couvre les deux formes : "jobseeker" (onboarding) et "job_seeker" (enum DB).
_ROLE_TO_INTENT: dict[str, str] = {
    "student":      "scholarship",   # bourses, grants, formations
    "jobseeker":    "career",        # emplois, opportunités, formations pro
    "job_seeker":   "career",
    "professional": "career",        # emplois, formations, opportunités
}

# Poids du score sémantique (cosine) dans le score final. La similarité
# cosinus est dans [0, 1] pour des textes ; on la projette sur 50 pour
# que le scoring profil (domaine +15, niveau +5, pays +5) ne masque pas
# le ranking sémantique mais départage des offres très proches.
_COSINE_SCORE_WEIGHT = 50.0

# Seuil minimum de similarité cosinus sous lequel une offre est considérée
# trop dissimilaire pour être retournée. Validé empiriquement :
# bourse↔scholarship ≈ 0.84, bourse↔cuisine ≈ 0.32 → seuil 0.35 exclut le bruit.
# Si tous les candidats sont sous ce seuil, on bascule automatiquement en ILIKE.
_MIN_COSINE_SIM = 0.35


class ScrapedOfferService:
    def __init__(self, db: Session) -> None:
        self.repo = ScrapedOfferRepository(db)

    def search_for_agent(
        self,
        intent: str,
        profile: dict,
        message: str,
        limit: int = 5,
    ) -> list[dict]:
        """Recherche classique pour enrichir le contexte d'un agent en cours de chat.

        Utilisé dans ChatService._enrich_with_offers().
        Retourne [] si l'intent n'est pas pertinent ou si la DB est vide.
        """
        if intent in _SKIP_INTENTS:
            return []

        country = profile.get("country")
        offers = self.repo.search_for_agent(
            intent=intent,
            country=country,
            limit=limit,
        )

        return [
            {
                "title": o.title,
                "url": o.url,
                "company": o.company,
                "location": o.location,
                "type": o.offer_type.value if o.offer_type else None,
            }
            for o in offers
            if o.title
        ]

    def search_for_profile(
        self,
        primary_role: str | None,
        domain_keywords: list[str],
        country: str | None,
        limit: int = 20,
    ) -> list[dict]:
        """Recommandations de bienvenue basées sur le profil d'inscription (cold start).

        Appelé par /recommendations/propositions quand aucune UserIntent n'existe encore
        (l'utilisateur n'a pas atteint EXTRACTION_THRESHOLD = 8 messages dans un thread).

        Stratégie :
        1. Mappe primary_role → intent_type → offer_types via _ROLE_TO_INTENT
           et le _INTENT_OFFER_TYPES déjà défini dans le repo (pas de duplication)
        2. Filtre par pays si disponible
        3. Score de pertinence Python post-fetch : +0.15 par mot-clé domaine/filière
           trouvé dans titre + description (granulé, pas bloquant)
        4. Retourne des dicts sérialisés avec offer_ref (compatible RecommendationItem)
        """
        intent_key = _ROLE_TO_INTENT.get((primary_role or "").lower())
        if not intent_key:
            return []

        # Récupère les offres du bon type via le repo existant (dedup + qualité inclus)
        raw: list["ScrapedOffer"] = self.repo.search_for_agent(
            intent=intent_key,
            country=country,
            limit=limit * 3,  # large pour laisser de la place au scoring Python
        )

        # Score de pertinence : base 0.5, +0.15 par mot-clé domaine/filière présent
        scored: list[tuple["ScrapedOffer", float]] = []
        for offer in raw:
            if not offer.title:
                continue
            text = f"{offer.title} {offer.description or ''}".lower()
            kw_hits = sum(1 for kw in domain_keywords if kw and kw.lower() in text)
            score = 0.5 + kw_hits * 0.15
            scored.append((offer, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # Seuil de pertinence : si l'utilisateur a renseigné un domaine/filière,
        # n'afficher que les offres qui ont au moins une correspondance (score > 0.5).
        # Mieux vaut l'empty state que des offres génériques sans rapport avec le profil.
        # Si aucun mot-clé fourni (profil incomplet), retourner les meilleures sans filtre.
        if domain_keywords:
            relevant = [(o, s) for o, s in scored if s > 0.5]
            if relevant:
                return [_serialize(o, s) for o, s in relevant[:limit]]
            return []  # Pas d'offre pertinente → empty state honnête

        return [_serialize(o, s) for o, s in scored[:limit]]

    async def search_for_matching(
        self,
        intent: "UserIntent",
        limit: int = 20,
    ) -> list[dict]:
        """Recherche sémantique enrichie sur une intention extraite.

        Pipeline :
        1. Embed le résumé d'intention (intent_summary) via EmbeddingService.
        2. Si embed disponible ET au moins une offre indexée : recherche
           pgvector cosine + scoring profil + filtre seuil _MIN_COSINE_SIM.
        3. Si résultat vide (tous sous le seuil) ou embed indisponible :
           fallback ILIKE (search_by_keywords) — comportement précédent préservé.

        Retourne les offres triées par score décroissant.
        """
        offer_type_filter = _intent_type_to_offer_types(intent.intent_type)
        query_text = _build_query_text(intent)
        query_vec = await self._embed_query(query_text)

        # Décider du mode : sémantique si embed OK ET offres indexées présentes.
        use_semantic = bool(query_vec) and self.repo.count_with_embedding() > 0
        if use_semantic:
            results = self._semantic_search(
                intent=intent,
                query_vec=query_vec,
                country=intent.location,
                offer_types=offer_type_filter,
                limit=limit,
            )
            if results:
                return results
            # Tous les candidats étaient sous _MIN_COSINE_SIM → fallback ILIKE
            logger.info(
                "[ScrapedOfferService] semantic search: 0 résultats au-dessus du seuil "
                "(intent_type=%s) — fallback ILIKE",
                intent.intent_type,
            )
        return self._keyword_search(
            intent=intent,
            offer_types=offer_type_filter,
            limit=limit,
        )

    # ── Privé ──────────────────────────────────────────────────────────────────

    async def _embed_query(self, text: str) -> list[float] | None:
        """Embed le texte de la requête. None si service indisponible / erreur."""
        if not text:
            return None
        svc = get_embedding_service()
        if not svc.available:
            return None
        try:
            return await svc.embed(text)
        except Exception:
            logger.warning("Embedding query failed — falling back to ILIKE", exc_info=True)
            return None

    def _semantic_search(
        self,
        *,
        intent: "UserIntent",
        query_vec: list[float],
        country: str | None,
        offer_types: list[str] | None,
        limit: int,
    ) -> list[dict]:
        """Branche pgvector — cosine + scoring profil + filtre seuil minimum.

        Retourne [] si tous les candidats sont sous _MIN_COSINE_SIM.
        L'appelant (search_for_matching) bascule alors en ILIKE.
        """
        # On élargit avant scoring profil pour ne pas tronquer prématurément.
        candidates = self.repo.search_by_embedding(
            query_vec=query_vec,
            country=country,
            offer_types=offer_types,
            limit=limit * 2,
        )

        scored: list[tuple["ScrapedOffer", float]] = []
        for offer, distance in candidates:
            if not offer.title:
                continue
            cosine_sim = max(0.0, 1.0 - distance)
            if cosine_sim < _MIN_COSINE_SIM:
                # Candidats retournés par pgvector triés par distance croissante :
                # dès qu'on passe sous le seuil, les suivants sont encore moins bons.
                break
            profile_bonus = _profile_score(offer, intent)
            score = cosine_sim * _COSINE_SCORE_WEIGHT + profile_bonus
            scored.append((offer, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [_serialize(o, score) for o, score in scored[:limit]]

    def _keyword_search(
        self,
        *,
        intent: "UserIntent",
        offer_types: list[str] | None,
        limit: int,
    ) -> list[dict]:
        """Branche ILIKE — fallback. Préserve le comportement historique."""
        search_terms: list[str] = []
        if intent.keywords:
            search_terms.extend(kw for kw in intent.keywords if kw)
        if intent.domain and intent.domain not in search_terms:
            search_terms.append(intent.domain)
        if intent.level and intent.level not in search_terms:
            search_terms.append(intent.level)

        offers = self.repo.search_by_keywords(
            terms=search_terms,
            country=intent.location,
            offer_types=offer_types,
            limit=limit,
        )

        scored = [
            (o, _profile_score(o, intent)) for o in offers if o.title
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [_serialize(o, score) for o, score in scored]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_query_text(intent: "UserIntent") -> str:
    """Concatène les champs de l'intention pour l'embedding.

    Préfère intent_summary (déjà synthétisé par le LLM). Si absent ou
    trop court, complète avec domain / level / location / keywords.
    """
    parts: list[str] = []
    summary = (intent.intent_summary or "").strip()
    if summary:
        parts.append(summary)

    extras: list[str] = []
    if intent.domain:
        extras.append(intent.domain)
    if intent.level:
        extras.append(intent.level)
    if intent.location:
        extras.append(intent.location)
    if intent.keywords:
        extras.extend(kw for kw in intent.keywords if kw)
    if extras:
        parts.append(" ".join(extras))

    return "\n".join(parts).strip()


def _intent_type_to_offer_types(intent_type: str | None) -> list[str] | None:
    """Mappe intent_type normalisé (IntentExtractorService.VALID_INTENT_TYPES)
    → liste d'offer_type ScrapedOffer pertinents.

    "reconversion" et "autre" étaient absents de ce mapping : intent_type
    retournait None → aucun filtre de type appliqué → la recherche mélangeait
    bourses, partenariats, ressources... avec les offres réellement pertinentes.
    "reconversion" (changement de métier) couvre job + formation + opportunity,
    comme "career" dans _INTENT_OFFER_TYPES (même logique, vocabulaire différent
    car intent_extractor utilise sa propre taxonomie). "autre" reste sans
    filtre : par définition aucun type ne lui correspond clairement.
    """
    mapping: dict[str, list[str]] = {
        "stage":           ["job"],           # internship ≈ job dans scraped_offers
        "emploi":          ["job"],
        "bourse":          ["scholarship"],
        "financement":     ["grant"],
        "appel_offre":     ["call_for_applications"],
        "formation":       ["formation"],
        "reconversion":    ["job", "formation", "opportunity"],
        "entrepreneuriat": ["opportunity"],
        "partenariat":     ["partnership"],
    }
    return mapping.get(intent_type or "", None)


# Aliases de domaines : associe les variantes courantes à un terme pivot.
# Utilisé dans _profile_score pour détecter des correspondances indirectes
# (ex: intent.domain="génie civil" → texte contient "btp" → bonus alias).
# Clés et valeurs en minuscules ; vérification via `alias in text`.
_DOMAIN_ALIASES: dict[str, list[str]] = {
    "informatique":        ["software", "développement", "developer", "tech ", "numérique", "digital", "coding", "programmation", "it "],
    "génie civil":         ["btp", "construction", "travaux publics", "civil engineering", "infrastructure", "bâtiment"],
    "finance":             ["comptabilité", "accounting", "financial", "banque", "banking", "trésorerie", "audit"],
    "marketing":           ["communication", "digital marketing", "growth", "brand", "commercial"],
    "agriculture":         ["agritech", "agroalimentaire", "agronomie", "farming", "agricultural"],
    "santé":               ["médecine", "health", "medical", "pharmaceutical", "pharmacie", "clinique"],
    "éducation":           ["enseignement", "teaching", "pédagogie", "école", "academic"],
    "entrepreneuriat":     ["startup", "business", "innovation", "incubateur", "entrepreneur"],
    "droit":               ["juridique", "legal", "law", "avocat", "juriste", "compliance"],
    "ressources humaines": ["rh", "hr", "human resources", "recrutement", "people management"],
    "énergie":             ["énergie renouvelable", "solar", "solaire", "electricity", "électricité", "green energy"],
    "environnement":       ["développement durable", "sustainability", "climat", "climate", "écologie"],
    "logistique":          ["supply chain", "transport", "warehouse", "entrepôt", "fret"],
    "immobilier":          ["real estate", "property", "foncier", "promotion immobilière"],
}


def _profile_score(offer: "ScrapedOffer", intent: "UserIntent") -> float:
    """Score de proximité offre ↔ profil (domaine, niveau, pays).

    Côté sémantique : additionné à cosine_sim × 50.
    Côté ILIKE : utilisé seul comme critère de tri.

    Stratégie domaine en 3 niveaux (du plus précis au plus large) :
    1. Correspondance directe intent.domain → +15
    2. Alias de domaine (_DOMAIN_ALIASES) → +12 (si pas de correspondance directe)
    3. Mots-clés LLM (intent.keywords, plus riches en synonymes) → +5 à +10 (fallback)

    Localisation : bonus partiel +2 pour les offres ouvertes à l'international
    quand la localisation exacte ne matche pas.
    """
    score = 0.0
    title = (offer.title or "").lower()
    description = (offer.description or "").lower()
    text = f"{title} {description}"
    location = (offer.location or "").lower()

    # ── Domaine ────────────────────────────────────────────────────────────
    domain_matched = False
    if intent.domain:
        domain_lower = intent.domain.lower()
        if domain_lower in text:
            score += 15.0
            domain_matched = True
        if not domain_matched:
            for alias in _DOMAIN_ALIASES.get(domain_lower, []):
                if alias in text:
                    score += 12.0
                    domain_matched = True
                    break

    # Fallback keywords LLM : extraits depuis la conversation, contiennent
    # souvent des synonymes et variantes non couverts par _DOMAIN_ALIASES.
    if not domain_matched and intent.keywords:
        kw_hits = sum(
            1 for kw in intent.keywords
            if kw and len(kw) > 2 and kw.lower() in text
        )
        if kw_hits >= 2:
            score += 10.0
        elif kw_hits == 1:
            score += 5.0

    # ── Niveau ─────────────────────────────────────────────────────────────
    if intent.level and intent.level.lower() in text:
        score += 5.0

    # ── Localisation ───────────────────────────────────────────────────────
    if intent.location:
        if intent.location.lower() in location:
            score += 5.0
        elif any(term in location for term in ("international", "global", "remote", "africa", "afrique", "monde")):
            # Offre ouverte internationalement : accessible même si la localisation
            # exacte de l'utilisateur n'est pas mentionnée → bonus partiel.
            score += 2.0

    return score


def _serialize(offer: "ScrapedOffer", score: float) -> dict:
    """Forme de dict consommée par les routers (recommendations, /pour-moi)."""
    return {
        "id": str(offer.id),
        "offer_ref": f"scraped:{offer.id}",
        "title": offer.title,
        "url": offer.url,
        "company": offer.company,
        "location": offer.location,
        "description": offer.description,
        "type": offer.offer_type.value if offer.offer_type else None,
        "scraped_at": offer.scraped_at,
        "relevance_score": round(score, 2),
    }
