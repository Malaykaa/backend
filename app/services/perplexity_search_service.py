"""Service de recherche web en temps réel via Perplexity sonar/sonar-pro.

Comportement défensif identique à EmbeddingService :
- PERPLEXITY_API_KEY absent → log warning + retourne None, jamais de crash.
- Timeout HTTP strict (8s) → ne bloque JAMAIS un agent.
- Cache Redis avec TTL par goal_type → 0 appel redondant dans la fenêtre.
- Fallback silencieux : si Redis indisponible, on appelle l'API sans cache.

Intégration dans le pipeline agent via goal_context["search_results"].
Le service est agnostique du domaine — c'est search_trigger.py qui décide
quand l'appeler et avec quelle query.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_PERPLEXITY_CHAT_URL = "https://api.perplexity.ai/chat/completions"

# Budget temps : sonar-pro peut prendre jusqu'à 12s sur des requêtes complexes.
# On accepte ce délai car le gain en qualité en vaut la peine pour scholarship/tender.
_HTTP_TIMEOUT = 15.0

# TTL cache Redis par goal_type (secondes)
# Logique : plus une info est volatile, plus le TTL est court.
_TTL_BY_GOAL_TYPE: dict[str, int] = {
    "tender":      2 * 3600,   # AO publiés fréquemment → 2h
    "career":      3 * 3600,   # Offres d'emploi → 3h
    "scholarship": 4 * 3600,   # Deadlines bougent peu dans la journée → 4h
    "funding":     4 * 3600,   # Critères stables → 4h
    "freelance":   3 * 3600,
    "exam":        6 * 3600,   # Dates officielles très stables → 6h
    "study_grant": 4 * 3600,
}
_TTL_DEFAULT = 3 * 3600


@dataclass
class SearchResult:
    """Résultat structuré d'une recherche Perplexity."""

    content: str           # Réponse synthétisée par le modèle
    citations: list[str]   # URLs sources (max 10 côté Perplexity)
    query: str             # Query exacte envoyée
    model: str             # "sonar" | "sonar-pro"
    cached: bool           # True si issu du cache Redis
    latency_ms: int        # 0 si cached

    def to_goal_context(self) -> dict:
        """Sérialise pour injection dans AgentContext.goal_context.

        N'expose QUE ce qui est utile au modèle. Les métriques internes —
        latency_ms, cached — restent en dehors : elles n'aident en rien la
        réponse et n'auraient pour effet que d'encombrer le prompt. `cached`
        s'y était glissé alors que `latency_ms` en avait été exclu ; les deux
        relèvent de la même catégorie.
        """
        return {
            "content": self.content,
            "citations": self.citations,
            "query": self.query,
            "model": self.model,
        }


class PerplexitySearchService:
    """Recherche web temps réel via Perplexity sonar.

    Singleton — instancier via get_perplexity_search_service().
    Le service est thread-safe (pas d'état mutable après __init__).
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.perplexity_api_key
        self._available = bool(self._api_key)
        self._redis = None  # initialisé lazily pour éviter un import circulaire

        if not self._available:
            logger.warning(
                "[PerplexitySearch] PERPLEXITY_API_KEY absent — "
                "recherche web temps réel désactivée. "
                "Les agents répondront sans données fraîches."
            )

    @property
    def available(self) -> bool:
        return self._available

    # ── Cache Redis ────────────────────────────────────────────────────────

    def _get_redis(self):
        """Retourne un client Redis synchrone ou None si indisponible."""
        if self._redis is not None:
            return self._redis
        try:
            import redis as redis_lib  # noqa: PLC0415
            settings = get_settings()
            if settings.redis_url:
                client = redis_lib.from_url(settings.redis_url, decode_responses=True)
                client.ping()  # valide la connexion au premier appel
                self._redis = client
        except Exception as exc:
            logger.debug("[PerplexitySearch] Redis indisponible pour le cache : %s", exc)
        return self._redis

    def _cache_key(self, query: str, model: str) -> str:
        digest = hashlib.md5(f"{model}:{query}".encode(), usedforsecurity=False).hexdigest()
        return f"pplx:search:{digest}"

    def _read_cache(self, key: str) -> SearchResult | None:
        try:
            redis = self._get_redis()
            if redis and (raw := redis.get(key)):
                data = json.loads(raw)
                data["cached"] = True
                data["latency_ms"] = 0
                return SearchResult(**data)
        except Exception as exc:
            logger.debug("[PerplexitySearch] cache read error: %s", exc)
        return None

    def _write_cache(self, key: str, result: SearchResult, ttl: int) -> None:
        try:
            redis = self._get_redis()
            if redis:
                payload = {k: v for k, v in asdict(result).items()
                           if k not in ("cached", "latency_ms")}
                redis.setex(key, ttl, json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            logger.debug("[PerplexitySearch] cache write error: %s", exc)

    # ── API Perplexity ─────────────────────────────────────────────────────

    async def _call_api(self, query: str, model: str) -> SearchResult:
        """POST /chat/completions avec return_citations=True."""
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Tu es un assistant de recherche. Réponds en français. "
                        "Sois factuel, précis et concis. "
                        "Concentre-toi sur les informations récentes et vérifiables."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "return_citations": True,
            "search_recency_filter": "month",
            "max_tokens": 800,
        }

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                _PERPLEXITY_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        citations = data.get("citations", [])

        return SearchResult(
            content=content,
            citations=citations[:10],
            query=query,
            model=model,
            cached=False,
            latency_ms=0,  # rempli par l'appelant
        )

    # ── Méthode publique ───────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        model: str = "sonar",
        goal_type: str | None = None,
    ) -> SearchResult | None:
        """Recherche web temps réel avec cache Redis.

        Args:
            query:     Requête contextualisée (pays, domaine, année).
            model:     "sonar" (rapide, ~2s) ou "sonar-pro" (profond, ~4s).
            goal_type: Détermine le TTL de cache.

        Returns:
            SearchResult ou None si service indisponible ou erreur.
            Le caller ne doit JAMAIS crasher sur None — c'est le comportement normal
            quand la clé API est absente ou que Perplexity est en erreur.
        """
        if not self._available:
            return None

        cache_key = self._cache_key(query, model)

        # 1. Cache hit
        if cached := self._read_cache(cache_key):
            logger.debug("[PerplexitySearch] cache hit (model=%s): %.60s", model, query)
            return cached

        # 2. Appel API avec timeout strict
        ttl = _TTL_BY_GOAL_TYPE.get(goal_type or "", _TTL_DEFAULT)
        t0 = time.monotonic()
        try:
            result = await self._call_api(query, model)
            result.latency_ms = int((time.monotonic() - t0) * 1000)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "[PerplexitySearch] HTTP %s: %s",
                exc.response.status_code, exc.response.text[:200],
            )
            return None
        except httpx.TimeoutException:
            logger.warning(
                "[PerplexitySearch] timeout (%.1fs) pour query: %.60s", _HTTP_TIMEOUT, query
            )
            return None
        except httpx.HTTPError as exc:
            logger.warning("[PerplexitySearch] réseau: %s", exc)
            return None
        except Exception as exc:
            logger.error("[PerplexitySearch] inattendu: %s", exc)
            return None

        logger.info(
            "[PerplexitySearch] model=%s latency=%dms citations=%d cached=False | %.60s",
            model, result.latency_ms, len(result.citations), query,
        )

        # 3. Mise en cache
        self._write_cache(cache_key, result, ttl)

        return result


# ── Singleton ──────────────────────────────────────────────────────────────

_singleton: PerplexitySearchService | None = None


def get_perplexity_search_service() -> PerplexitySearchService:
    """Singleton — reflète PERPLEXITY_API_KEY au boot."""
    global _singleton
    if _singleton is None:
        _singleton = PerplexitySearchService()
    return _singleton
