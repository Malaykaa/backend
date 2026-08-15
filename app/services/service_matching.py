"""Matching demandes ↔ prestataires.

Ce module ne modifie AUCUN code existant du matching d'offres. Il importe les
primitives déjà écrites et testées, et les applique à de nouveaux objets : les
modèles exposent volontairement `title`, `normalized_title` et `description`,
les trois attributs que `_term_coverage` lit par typage canard.

Deux viviers, jamais mélangés :
- `provider` : vitrines publiées, interrogées à la création de la demande ;
- `public`   : tous les utilisateurs, uniquement si le client élargit lui-même.

La géographie est un FILTRE, pas un bonus de score
───────────────────────────────────────────────────
Le prestataire a toujours une ville et un pays : il est forcément quelque
part. C'est le CLIENT qui décide, en créant sa demande, si cette localisation
compte — via `ServiceRequest.delivery_mode` :

- `remote`           : aucun filtre. La localisation des prestataires est
  ignorée, le classement se fait uniquement sur la pertinence au besoin.
- `onsite` / `hybrid` : filtre réel, appliqué en SQL avant même que le
  classement par pertinence ne commence — d'abord le pays, puis la ville
  quand elle est précisée. Un prestataire hors zone n'est jamais candidat,
  quel que soit son score de pertinence.

Ce choix — filtrer plutôt que pondérer — élimine une classe entière de bug :
avec un bonus, la géographie pouvait à elle seule faire remonter un profil
hors sujet (un développeur à Abidjan sur une demande de plomberie). Avec un
filtre, elle ne fait jamais gagner de terrain à un candidat non pertinent :
elle décide seulement qui est éligible, la pertinence décide seule du reste.
Si le filtre ne laisse personne, le client garde la main : il peut élargir sa
demande au grand public (cf. `can_go_public` dans le routeur).
"""

from __future__ import annotations

import logging
import unicodedata
import re
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.service import (
    DeliveryMode, MatchDecision, MatchSource, ProviderStatus, ServiceProvider,
    ServiceRequest, ServiceRequestMatch,
)
from app.models.user import Profile, User
from app.models.user_intent import UserIntent
from app.services.embedding_service import get_embedding_service
# Primitives partagées avec le matching d'offres. Importées volontairement
# plutôt que réécrites : toute correction faite là-bas (pondération titre,
# frontières de mot, bonus de corroboration borné) vaut automatiquement ici.
from app.services.scraped_offer_service import MATCH_MODE_LEXICAL, MATCH_MODE_SEMANTIC, _fuse_results, _term_coverage

logger = logging.getLogger(__name__)

# Nombre de destinataires sollicités par vague. Sans plafond, une demande vague
# partirait à tout le monde et brûlerait le réseau dès la première semaine.
MAX_RECIPIENTS_PER_WAVE = 8

# Vivier interrogé avant scoring — large, car le classement fin se fait
# ensuite en Python. Le filtrage géographique, lui, a déjà eu lieu en SQL :
# ce plafond porte donc uniquement sur des candidats déjà éligibles.
_CANDIDATE_POOL = MAX_RECIPIENTS_PER_WAVE * 6

# Seuil sous lequel on ne sollicite personne : mieux vaut proposer au client
# d'élargir que de notifier des prestataires hors sujet, qui se désabonneront.
# Porte uniquement sur la pertinence désormais, la géographie étant un filtre
# préalable et non plus une composante du score.
MIN_MATCH_SCORE = 25.0


def _flatten_json_field(value) -> list[str]:
    """Extrait des termes texte d'un champ `skills`/`goals` — dict ou liste.

    Ces deux champs de `Profile` n'ont jamais eu de forme unique : selon
    l'étape d'inscription qui les a écrits, ce sont des listes de chaînes ou
    des dictionnaires (`{"Python": "avancé"}`). On prend tout ce qui est
    exploitable des deux côtés — clés et valeurs — plutôt que de parier sur
    une forme précise et perdre l'information si elle ne correspond pas.
    """
    if isinstance(value, dict):
        return [str(v) for v in list(value.keys()) + list(value.values()) if v]
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


def _profile_text(profile: Profile | None) -> str:
    """Ce qu'un utilisateur du grand public a déclaré à l'inscription.

    Le matching grand public (`match_public`) ne se basait jusqu'ici que sur
    les intentions déclarées (`UserIntent`) — un texte court, généré à partir
    d'un seul échange. Un utilisateur peut très bien correspondre à une
    demande sans que son intention la plus récente le montre : son domaine,
    ses compétences ou ses objectifs déclarés à l'inscription en disent
    souvent plus, et ne coûtent rien à interroger puisque le profil est déjà
    chargé pour le filtre géographique.
    """
    if not profile:
        return ""
    parts = [
        profile.domain, profile.field_of_study, profile.current_status,
        profile.preferred_content,
        *_flatten_json_field(profile.skills),
        *_flatten_json_field(profile.goals),
    ]
    return " ".join(p for p in parts if p)


def _apply_geo_filter(
    query, request: ServiceRequest, *, city_col: ColumnElement, country_col: ColumnElement,
):
    """Restreint une requête SQL au pays/ville voulus par le client, si voulus.

    Générique sur la colonne à filtrer : sert aussi bien pour les vitrines
    prestataires (`ServiceProvider`) que pour les profils du grand public
    (`Profile`), qui portent chacun leurs propres colonnes ville/pays.

    Une comparaison qui porte sur une colonne NULL ne satisfait jamais un
    filtre SQL — un profil sans ville renseignée est donc naturellement exclu
    dès qu'une ville est exigée, sans traitement particulier à écrire.
    """
    if request.delivery_mode == DeliveryMode.remote:
        return query
    if request.country:
        query = query.where(func.lower(country_col) == request.country.strip().lower())
    if request.city:
        query = query.where(city_col.ilike(f"%{request.city.strip()}%"))
    return query


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


class _Candidate:
    """Adaptateur minimal exposant les attributs attendus par `_term_coverage`."""

    __slots__ = ("title", "normalized_title", "description")

    def __init__(self, title: str | None, normalized_title: str | None, description: str | None):
        self.title = title
        self.normalized_title = normalized_title
        self.description = description


def _score(
    *, terms: list[str],
    title: str | None, normalized_title: str | None, description: str | None,
    cosine: float | None = None,
) -> float:
    """Score 0–100, pure pertinence au besoin exprimé.

    La géographie n'entre plus dans le score : elle a déjà filtré les
    candidats en amont (`_apply_geo_filter`) quand le client l'a demandé. Un
    candidat qui atteint ce point a déjà passé ce filtre — le score ne
    départage donc plus que sur la compétence, jamais sur la localisation.
    """
    relevance = (
        cosine if cosine is not None
        else _term_coverage(_Candidate(title, normalized_title, description), terms)
    )
    return 100.0 * relevance


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

    def match_providers_lexical(
        self, request: ServiceRequest, *, limit: int = MAX_RECIPIENTS_PER_WAVE,
    ) -> list[dict]:
        """Variante purement lexicale — aucun appel réseau, réponse immédiate.

        Utilisée sur le chemin de requête, là où l'utilisateur attend. La voie
        sémantique passe ensuite en tâche de fond : elle ajoute des candidats
        sans jamais retarder la réponse.
        """
        terms = build_search_terms(
            title=request.title, description=request.description, keywords=request.keywords,
        )
        base = select(ServiceProvider).where(
            ServiceProvider.status == ProviderStatus.published,
            ServiceProvider.user_id != request.requester_id,
        )
        base = _apply_geo_filter(
            base, request, city_col=ServiceProvider.city, country_col=ServiceProvider.country,
        )
        results: list[dict] = []
        for p in self.db.execute(base.limit(_CANDIDATE_POOL)).scalars().all():
            score = _score(
                terms=terms,
                title=p.title, normalized_title=p.normalized_title, description=p.description,
            )
            if score >= MIN_MATCH_SCORE:
                results.append(self._as_result(p, score, MATCH_MODE_LEXICAL))

        results.sort(key=lambda r: r["match_score"], reverse=True)
        logger.info(
            "[ServiceMatching] demande=%s lexical : %d retenu(s)", request.id, len(results[:limit]),
        )
        return results[:limit]

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
        base = _apply_geo_filter(
            base, request, city_col=ServiceProvider.city, country_col=ServiceProvider.country,
        )

        lexical: list[dict] = []
        for p in self.db.execute(base.limit(_CANDIDATE_POOL)).scalars().all():
            score = _score(
                terms=terms,
                title=p.title, normalized_title=p.normalized_title, description=p.description,
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
                    terms=terms,
                    title=p.title, normalized_title=p.normalized_title, description=p.description,
                    cosine=cosine,
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

        # Même filtre géographique que pour les vitrines : un profil sans
        # ville/pays renseigné ne peut pas prouver qu'il est dans la zone
        # demandée, et une comparaison sur colonne NULL ne satisfait jamais
        # le filtre — il est donc naturellement exclu, sans cas particulier.
        query = select(UserIntent, Profile).outerjoin(Profile, Profile.user_id == UserIntent.user_id)
        query = _apply_geo_filter(query, request, city_col=Profile.city, country_col=Profile.country)
        rows = self.db.execute(
            query
            .where(UserIntent.user_id != request.requester_id)
            .order_by(UserIntent.extracted_at.desc())
            .limit(_CANDIDATE_POOL * 3)
        ).all()

        best: dict[str, dict] = {}
        for intent, profile in rows:
            if intent.user_id in already:
                continue
            # L'intention la plus récente prime pour le titre — c'est le signal
            # le plus direct. Le profil vient l'épauler dans la description :
            # une personne peut correspondre par ce qu'elle a déclaré à
            # l'inscription (domaine, compétences, objectifs) sans que ça
            # ressorte de sa toute dernière intention.
            description = " ".join(filter(None, [
                intent.intent_summary, " ".join(intent.keywords or []), _profile_text(profile),
            ]))
            score = _score(
                terms=terms,
                title=intent.domain or "",
                normalized_title=None,
                description=description,
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
