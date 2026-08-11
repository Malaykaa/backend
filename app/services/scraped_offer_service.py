"""Service scraped_offers — recherche d'offres pertinentes pour enrichir le contexte agent.

Deux modes :
- search_for_agent()   : recherche classique par intent + country (contexte chat)
- search_for_matching(): recherche sémantique pgvector (cosine) + scoring profil,
                         avec fallback ILIKE si EmbeddingService indisponible
                         ou si aucune offre n'est encore indexée.
"""

from __future__ import annotations

import logging
import re
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

# ── Échelle commune de matching (0–100) ────────────────────────────────────
#
# `relevance_score` reste inchangé : les routers de recommandation font de
# l'arithmétique absolue dessus (bonus niveau, deltas de feedback, bonus LLM)
# et changer son échelle fausserait leurs pondérations.
#
# `match_score` est le NOUVEAU champ normalisé, comparable entre les deux
# modes de recherche. C'est lui — et lui seul — qui doit servir à décider
# d'une notification ou à afficher un pourcentage à l'utilisateur.
#
# Composition identique dans les deux modes : deux tiers pour le signal de
# pertinence à la requête, un tiers pour la proximité de profil. Ce ratio
# reprend celui déjà en vigueur côté sémantique (cosine ×50 contre profil
# plafonné à 25), pour que le mode dégradé ne change pas les équilibres.
_RELEVANCE_WEIGHT = 2.0 / 3.0
_PROFILE_WEIGHT = 1.0 / 3.0

# Plafond théorique de _profile_score : domaine 15 + niveau 5 + localisation 5.
# Les bonus mots-clés (10 / 5) sont exclusifs du bonus domaine, ils ne
# s'additionnent jamais — le maximum reste donc 25.
_PROFILE_MAX = 25.0

# Pondération d'un terme trouvé dans le titre plutôt que dans la description.
# Un intitulé qui contient le mot cherché est un signal bien plus fort qu'une
# occurrence noyée dans un paragraphe.
_TERM_WEIGHT_TITLE = 1.0
_TERM_WEIGHT_DESCRIPTION = 0.45

MATCH_MODE_SEMANTIC = "semantic"
MATCH_MODE_LEXICAL = "lexical"
# Offre retrouvée par les deux voies : c'est le signal le plus fiable, deux
# méthodes indépendantes s'accordent.
MATCH_MODE_HYBRID = "hybrid"

# ── Fusion des deux voies ──────────────────────────────────────────────────
#
# La fusion de rangs (RRF, `Σ 1/(k + rang)`) est la réponse classique quand on
# combine des systèmes dont les scores ne sont pas comparables. Elle a été
# implémentée puis écartée ici, pour deux raisons mesurées :
#
# 1. Dans chaque liste, le rang est une fonction monotone de `match_score`
#    (sémantique : `raw = cosine×50 + profil`, `match_score = raw × 4/3` ;
#    lexical : tri direct sur `match_score`). Le rang n'apporte donc aucune
#    information que le score ne porte déjà — mais il en perd une, la magnitude.
# 2. Conséquence : une offre présente dans les deux listes (0.0325) devançait
#    systématiquement une offre solo mieux notée (0.0164), quel que soit
#    l'écart de pertinence. Une correspondance à 52 % passait devant une à 94 %.
#
# La phase 1 ayant rendu `match_score` comparable entre les modes, on fusionne
# donc sur le score, en traitant la corroboration comme ce qu'elle est : un
# renfort de confiance, pas un critère dominant.
#
# Le bonus est proportionnel à l'avis de la voie la plus faible : deux méthodes
# qui notent l'offre 90 et 85 se confortent réellement ; 90 et 12, beaucoup
# moins. Plafonné, il ne peut jamais renverser un écart de pertinence important.
_CORROBORATION_BONUS = 10.0

# Multiplicateur du vivier demandé à chaque branche avant fusion.
_FUSION_POOL_FACTOR = 3


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

        # Score de pertinence : base 0.5, +0.15 par mot-clé domaine/filière présent.
        # Cette échelle (0.5 → 1.x) est propre à ce chemin et conservée telle
        # quelle dans `relevance_score` ; `match_score` la reprojette sur 0–100.
        scored: list[tuple["ScrapedOffer", float, float]] = []
        for offer in raw:
            if not offer.title:
                continue
            text = f"{offer.title} {offer.description or ''}".lower()
            kw_hits = sum(
                1 for kw in domain_keywords if kw and _contains_word(text, kw.lower())
            )
            score = 0.5 + kw_hits * 0.15

            # La composante profil vaut plein tarif : le repo a déjà filtré sur
            # le type d'offre correspondant au rôle déclaré, cette part du
            # matching est donc acquise. La couverture des mots-clés module le
            # reste. Sans mot-clé (profil incomplet), on n'affiche que la
            # confiance minimale plutôt qu'un pourcentage inventé.
            coverage = (
                _term_coverage(offer, list(domain_keywords)) if domain_keywords else 0.0
            )
            match_score = 100.0 * (_RELEVANCE_WEIGHT * coverage + _PROFILE_WEIGHT)
            scored.append((offer, score, match_score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # Seuil de pertinence : si l'utilisateur a renseigné un domaine/filière,
        # n'afficher que les offres qui ont au moins une correspondance (score > 0.5).
        # Mieux vaut l'empty state que des offres génériques sans rapport avec le profil.
        # Si aucun mot-clé fourni (profil incomplet), retourner les meilleures sans filtre.
        if domain_keywords:
            relevant = [(o, s, ms) for o, s, ms in scored if s > 0.5]
            if relevant:
                return [
                    _serialize(o, s, match_score=ms, match_mode=MATCH_MODE_LEXICAL)
                    for o, s, ms in relevant[:limit]
                ]
            return []  # Pas d'offre pertinente → empty state honnête

        return [
            _serialize(o, s, match_score=ms, match_mode=MATCH_MODE_LEXICAL)
            for o, s, ms in scored[:limit]
        ]

    async def search_for_matching(
        self,
        intent: "UserIntent",
        limit: int = 20,
    ) -> list[dict]:
        """Recherche hybride sur une intention extraite.

        Les deux recherches sont lancées puis **fusionnées**, au lieu de
        choisir l'une ou l'autre. L'ancienne bascule « sémantique sinon ILIKE »
        avait un défaut grave : `search_by_embedding` ne considère que les
        offres dont l'embedding est non-NULL, et le repli ne se déclenchait que
        si le sémantique renvoyait zéro résultat. Tant que l'index était
        partiel, une correspondance médiocre parmi les offres indexées
        l'emportait donc définitivement sur une excellente correspondance
        parmi les non indexées.

        Avec la fusion, toute offre reste atteignable par au moins une des deux
        voies, quel que soit l'état de l'index.

        La fusion s'appuie sur `match_score`, rendu comparable entre les modes
        à l'étape précédente, et traite l'accord des deux voies comme un
        renfort de confiance borné (cf. `_fuse_results`).

        Sans clé d'embeddings, la liste sémantique est vide : la fusion se
        réduit à l'ordre lexical, strictement identique au comportement
        antérieur.
        """
        offer_type_filter = _intent_type_to_offer_types(intent.intent_type)
        query_text = _build_query_text(intent)
        query_vec = await self._embed_query(query_text)

        # Vivier élargi avant fusion : une offre peut être bien classée dans une
        # liste et absente de l'autre, il faut de la matière pour que le
        # recoupement ait du sens.
        pool = max(limit * _FUSION_POOL_FACTOR, limit)

        semantic: list[dict] = []
        # `count_with_embedding` n'est plus un aiguillage — juste une économie :
        # inutile d'interroger pgvector si rien n'est encore indexé.
        if query_vec and self.repo.count_with_embedding() > 0:
            semantic = self._semantic_search(
                intent=intent,
                query_vec=query_vec,
                country=intent.location,
                offer_types=offer_type_filter,
                limit=pool,
            )

        lexical = self._keyword_search(
            intent=intent,
            offer_types=offer_type_filter,
            limit=pool,
        )

        fused = _fuse_results(semantic, lexical, limit=limit)
        logger.info(
            "[ScrapedOfferService] hybride intent_type=%s : %d sémantique(s), "
            "%d lexicale(s) → %d fusionnée(s)",
            intent.intent_type, len(semantic), len(lexical), len(fused),
        )
        return fused

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

        scored: list[tuple["ScrapedOffer", float, float]] = []
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
            # Échelle commune : la similarité cosinus joue le rôle de signal de
            # pertinence, le profil celui de bonus contextuel.
            match_score = 100.0 * (
                _RELEVANCE_WEIGHT * cosine_sim
                + _PROFILE_WEIGHT * min(1.0, profile_bonus / _PROFILE_MAX)
            )
            scored.append((offer, score, match_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            _serialize(o, s, match_score=ms, match_mode=MATCH_MODE_SEMANTIC)
            for o, s, ms in scored[:limit]
        ]

    def _keyword_search(
        self,
        *,
        intent: "UserIntent",
        offer_types: list[str] | None,
        limit: int,
    ) -> list[dict]:
        """Branche ILIKE — repli lorsque le sémantique est indisponible.

        Le SQL sélectionne un vivier large (les termes sont combinés en OR),
        le classement fin se fait ici : couverture des termes de la requête
        d'abord, proximité de profil ensuite, `quality_score` en simple
        départage.
        """
        search_terms: list[str] = []
        if intent.keywords:
            search_terms.extend(kw for kw in intent.keywords if kw)
        if intent.domain and intent.domain not in search_terms:
            search_terms.append(intent.domain)
        if intent.level and intent.level not in search_terms:
            search_terms.append(intent.level)

        # On demande large avant de scorer : le SQL trie par quality_score, un
        # critère intrinsèque à l'offre et sans rapport avec la requête. Tronquer
        # à `limit` avant le classement revenait à reclasser une tranche
        # arbitraire. La branche sémantique élargit déjà de la même façon.
        offers = self.repo.search_by_keywords(
            terms=search_terms,
            country=intent.location,
            offer_types=offer_types,
            limit=limit * 3,
        )

        scored: list[tuple["ScrapedOffer", float, float]] = []
        for offer in offers:
            if not offer.title:
                continue
            profile_bonus = _profile_score(offer, intent)
            coverage = _term_coverage(offer, search_terms)
            match_score = 100.0 * (
                _RELEVANCE_WEIGHT * coverage
                + _PROFILE_WEIGHT * min(1.0, profile_bonus / _PROFILE_MAX)
            )
            scored.append((offer, profile_bonus, match_score))

        # Tri sur le score normalisé : il porte la couverture des termes, que
        # `profile_bonus` seul ignorait complètement.
        scored.sort(key=lambda x: x[2], reverse=True)
        return [
            _serialize(o, s, match_score=ms, match_mode=MATCH_MODE_LEXICAL)
            for o, s, ms in scored[:limit]
        ]


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


def _fuse_results(
    semantic: list[dict],
    lexical: list[dict],
    *,
    limit: int,
) -> list[dict]:
    """Fusionne les deux voies de recherche en un classement unique.

    Le `match_score` retenu est le **maximum** des deux, jamais leur moyenne :
    si la voie sémantique juge une offre à 85 et la lexicale à 40, l'offre vaut
    85. La seconde n'a pas su la reconnaître, ce qui n'est pas une preuve
    contraire — moyenner reviendrait à pénaliser une offre pour n'avoir été
    trouvée que par une méthode.

    Une offre repérée par les deux voies reçoit un bonus de corroboration
    proportionnel à l'avis de la plus faible : deux méthodes indépendantes qui
    s'accordent constituent le signal le plus fiable disponible. Le bonus reste
    borné, donc il départage des offres proches sans jamais faire passer une
    correspondance médiocre devant une bonne.

    Le tri de sortie porte sur ce score fusionné ; `match_score` conserve la
    confiance nue, celle qui sera comparée au seuil de notification.

    Cas dégradés :
    - une seule liste non vide → l'ordre d'origine est préservé à l'identique,
      puisque chaque branche trie déjà par score décroissant ;
    - les deux vides → liste vide.
    """
    if not semantic and not lexical:
        return []

    fused: dict[str, dict] = {}

    for source_mode, results in (
        (MATCH_MODE_SEMANTIC, semantic),
        (MATCH_MODE_LEXICAL, lexical),
    ):
        for offer in results:
            key = offer.get("offer_ref") or offer.get("id")
            if not key:
                continue

            score = float(offer.get("match_score") or 0.0)
            existing = fused.get(key)

            if existing is None:
                fused[key] = {**offer, "_peer": None, "match_mode": source_mode}
                continue

            # Vue par les deux voies : on garde la meilleure confiance et on
            # mémorise l'avis de la plus faible, qui dosera le bonus.
            previous = float(existing.get("match_score") or 0.0)
            if score > previous:
                existing["match_score"] = score
                existing["relevance_score"] = offer.get(
                    "relevance_score", existing.get("relevance_score")
                )
                existing["_peer"] = previous
            else:
                existing["_peer"] = score
            existing["match_mode"] = MATCH_MODE_HYBRID

    def _fusion_score(offer: dict) -> float:
        base = float(offer.get("match_score") or 0.0)
        peer = offer.get("_peer")
        if peer is None:
            return base
        return base + _CORROBORATION_BONUS * (float(peer) / 100.0)

    ordered = sorted(fused.values(), key=_fusion_score, reverse=True)
    for offer in ordered:
        offer.pop("_peer", None)
    return ordered[:limit]


_WORD_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _contains_word(haystack: str, needle: str) -> bool:
    """Cherche `needle` dans `haystack` en respectant les frontières de mot.

    L'opérateur `in` produisait des faux positifs coûteux : le domaine « art »
    matchait « start-up » et « partenariat », « ia » matchait « biais ». Plus le
    terme est court, plus le bonus devenait du bruit.

    Les frontières ne sont posées que du côté où le terme commence/finit par un
    caractère de mot : un besoin comme « c++ » ou « .net » reste trouvable.
    Les motifs sont mis en cache — cette fonction est appelée des milliers de
    fois par run de matching.
    """
    if not needle or not haystack:
        return False

    pattern = _WORD_RE_CACHE.get(needle)
    if pattern is None:
        escaped = re.escape(needle)
        prefix = r"\b" if needle[0].isalnum() or needle[0] == "_" else ""
        suffix = r"\b" if needle[-1].isalnum() or needle[-1] == "_" else ""
        try:
            pattern = re.compile(f"{prefix}{escaped}{suffix}")
        except re.error:  # pragma: no cover — re.escape rend ce cas improbable
            logger.warning("[Matching] motif invalide pour le terme %r", needle)
            return needle in haystack
        _WORD_RE_CACHE[needle] = pattern

    return pattern.search(haystack) is not None


def _term_coverage(offer: "ScrapedOffer", terms: list[str]) -> float:
    """Part des termes de la requête réellement présents dans l'offre, dans [0, 1].

    C'est le signal de pertinence de la branche lexicale — l'équivalent de la
    similarité cosinus côté sémantique. Sans lui, une offre qui matche un terme
    sur six pesait autant qu'une qui les matchait tous, puisque le tri se faisait
    sur `quality_score` (une propriété de l'offre, pas de la requête).

    Un terme trouvé dans le titre compte plein tarif, dans la seule description
    il compte moins : un intitulé qui porte le mot cherché est un signal bien
    plus fort qu'une occurrence noyée dans un paragraphe.

    Retourne 0.0 si aucun terme n'est exploitable, ce qui neutralise la
    composante pertinence et laisse le score reposer sur le profil seul.
    """
    usable = [t.lower() for t in terms if t and len(t.strip()) > 2]
    if not usable:
        return 0.0

    title = f"{offer.title or ''} {offer.normalized_title or ''}".lower()
    description = (offer.description or "").lower()

    total = 0.0
    for term in usable:
        if _contains_word(title, term):
            total += _TERM_WEIGHT_TITLE
        elif _contains_word(description, term):
            total += _TERM_WEIGHT_DESCRIPTION

    return min(1.0, total / len(usable))


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
        if _contains_word(text, domain_lower):
            score += 15.0
            domain_matched = True
        if not domain_matched:
            for alias in _DOMAIN_ALIASES.get(domain_lower, []):
                if _contains_word(text, alias):
                    score += 12.0
                    domain_matched = True
                    break

    # Fallback keywords LLM : extraits depuis la conversation, contiennent
    # souvent des synonymes et variantes non couverts par _DOMAIN_ALIASES.
    if not domain_matched and intent.keywords:
        kw_hits = sum(
            1 for kw in intent.keywords
            if kw and len(kw) > 2 and _contains_word(text, kw.lower())
        )
        if kw_hits >= 2:
            score += 10.0
        elif kw_hits == 1:
            score += 5.0

    # ── Niveau ─────────────────────────────────────────────────────────────
    if intent.level and _contains_word(text, intent.level.lower()):
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


def _serialize(
    offer: "ScrapedOffer",
    score: float,
    *,
    match_score: float,
    match_mode: str,
) -> dict:
    """Forme de dict consommée par les routers (recommendations, /pour-moi).

    Deux familles de score cohabitent volontairement :

    - `relevance_score` : score brut, propre à chaque mode, conservé tel quel
      pour ne pas fausser l'arithmétique absolue des recommandations (bonus
      niveau, deltas de feedback, bonus LLM). Non comparable entre modes.
    - `match_score` : normalisé 0–100, comparable entre modes. C'est celui à
      utiliser pour notifier ou afficher un pourcentage.

    `match_mode` indique laquelle des deux branches a produit le résultat, ce
    qui rend les envois diagnosticables a posteriori.
    """
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
        "match_score": round(max(0.0, min(100.0, match_score)), 1),
        "match_mode": match_mode,
    }
