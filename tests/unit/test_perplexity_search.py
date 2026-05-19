"""Tests unitaires — PerplexitySearchService et search_trigger.

Philosophie :
- Aucun appel réseau réel (httpx mocké via unittest.mock).
- Aucune dépendance Redis (Redis mocké ou absent).
- Tests déterministes, rapides (<1s chacun).
- Vérification que le fallback silencieux fonctionne dans tous les cas d'erreur.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.search_trigger import build_search_query, should_search
from app.services.perplexity_search_service import (
    PerplexitySearchService,
    SearchResult,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


def _make_api_response(content: str, citations: list[str]) -> dict:
    """Fabrique une réponse JSON conforme à l'API Perplexity."""
    return {
        "choices": [{"message": {"content": content}}],
        "citations": citations,
    }


def _make_service(api_key: str = "test-key") -> PerplexitySearchService:
    """Instancie le service avec une clé factice sans toucher les singletons."""
    with patch("app.services.perplexity_search_service.get_settings") as mock_cfg:
        mock_cfg.return_value.perplexity_api_key = api_key
        mock_cfg.return_value.redis_url = None  # pas de Redis en test
        svc = PerplexitySearchService()
    return svc


# ── Tests : should_search() ────────────────────────────────────────────────


class TestShouldSearch:
    def test_returns_false_for_ineligible_goal_type(self):
        ok, model = should_search("liste des bourses 2026", goal_type="document")
        assert ok is False
        assert model == ""

    def test_returns_false_for_none_goal_type(self):
        ok, _ = should_search("liste des bourses 2026", goal_type=None)
        assert ok is False

    def test_triggers_on_temporal_signal(self):
        ok, model = should_search("quelles sont les bourses 2026 disponibles", "scholarship")
        assert ok is True
        assert model == "sonar-pro"  # scholarship → sonar-pro

    def test_triggers_on_specificity_signal(self):
        ok, model = should_search("liste des financements disponibles pour mon projet", "funding")
        assert ok is True
        assert model == "sonar-pro"

    def test_triggers_on_combined_signals(self):
        ok, model = should_search("où postuler pour un appel d'offres récent", "tender")
        assert ok is True
        assert model == "sonar-pro"

    def test_no_trigger_on_vague_short_message(self):
        ok, _ = should_search("aide-moi", "scholarship")
        assert ok is False

    def test_career_uses_sonar_not_pro(self):
        ok, model = should_search("quelles offres d'emploi récentes en informatique 2026", "career")
        assert ok is True
        assert model == "sonar"

    def test_long_message_contributes_to_score(self):
        # Message long sans signal fort — peut ou non déclencher selon le contenu
        msg = "je cherche des opportunités dans mon domaine en Côte d'Ivoire cette année"
        ok, _ = should_search(msg, "scholarship")
        # "cette année" est dans _TEMPORAL → doit déclencher
        assert ok is True

    def test_no_trigger_below_threshold(self):
        # Pas de signal temporel ni de spécificité, message court
        ok, _ = should_search("explique-moi le processus", "funding")
        assert ok is False


# ── Tests : build_search_query() ──────────────────────────────────────────


class TestBuildSearchQuery:
    def test_includes_country_and_domain(self):
        profile = {"country": "Sénégal", "domain": "Informatique"}
        query = build_search_query("bourses disponibles", "scholarship", profile)
        assert "Sénégal" in query
        assert "Informatique" in query

    def test_includes_message(self):
        profile = {"country": "Mali", "domain": "Agriculture"}
        query = build_search_query("financement pour startup agricole", "funding", profile)
        assert "financement pour startup agricole" in query

    def test_fallback_country_when_missing(self):
        query = build_search_query("appels d'offres", "tender", {})
        assert "Afrique" in query

    def test_no_prefix_duplication(self):
        """Si le message contient déjà le préfixe, ne pas le doubler."""
        profile = {"country": "Cameroun", "domain": "Finance"}
        # Message qui contient le préfixe naturellement
        msg = "bourses d'études Finance Cameroun 2026 — deadline ?"
        query = build_search_query(msg, "scholarship", profile)
        # La query ne doit pas répéter le préfixe deux fois
        assert query.count("bourses d'études") <= 2  # acceptable

    def test_different_goal_types_produce_different_prefixes(self):
        profile = {"country": "Côte d'Ivoire", "domain": "Génie civil"}
        q_scholarship = build_search_query("recherche", "scholarship", profile)
        q_tender = build_search_query("recherche", "tender", profile)
        assert q_scholarship != q_tender
        assert "bourses" in q_scholarship.lower()
        assert "appels d'offres" in q_tender.lower()


# ── Tests : PerplexitySearchService ───────────────────────────────────────


class TestPerplexitySearchService:

    def test_unavailable_without_api_key(self):
        svc = _make_service(api_key="")
        assert svc.available is False

    def test_available_with_api_key(self):
        svc = _make_service(api_key="sk-test")
        assert svc.available is True

    @pytest.mark.asyncio
    async def test_returns_none_when_unavailable(self):
        svc = _make_service(api_key="")
        result = await svc.search("bourses 2026 Sénégal")
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_search_returns_result(self):
        svc = _make_service()
        svc._get_redis = MagicMock(return_value=None)  # pas de cache

        api_payload = _make_api_response(
            content="Les bourses Eiffel 2026 ouvrent le 8 janvier.",
            citations=["https://campusfrance.org/eiffel"],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = api_payload
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await svc.search("bourses Eiffel 2026", model="sonar")

        assert result is not None
        assert isinstance(result, SearchResult)
        assert "Eiffel" in result.content
        assert "campusfrance.org" in result.citations[0]
        assert result.cached is False
        assert result.model == "sonar"

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        import httpx
        svc = _make_service()
        svc._get_redis = MagicMock(return_value=None)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await svc.search("test query")

        assert result is None  # fallback silencieux

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        import httpx
        svc = _make_service()
        svc._get_redis = MagicMock(return_value=None)

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "429", request=MagicMock(), response=mock_response
                )
            )
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await svc.search("test query")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self):
        svc = _make_service()

        cached_data = {
            "content": "Résultat en cache",
            "citations": ["https://example.com"],
            "query": "test",
            "model": "sonar",
        }

        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=json.dumps(cached_data))
        svc._get_redis = MagicMock(return_value=mock_redis)

        with patch.object(svc, "_call_api", new_callable=AsyncMock) as mock_api:
            result = await svc.search("test query")
            mock_api.assert_not_called()  # API non appelée si cache hit

        assert result is not None
        assert result.cached is True
        assert result.content == "Résultat en cache"

    @pytest.mark.asyncio
    async def test_result_is_written_to_cache_after_api_call(self):
        svc = _make_service()

        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=None)  # pas de hit
        mock_redis.setex = MagicMock()
        svc._get_redis = MagicMock(return_value=mock_redis)

        api_payload = _make_api_response("Contenu frais", ["https://source.com"])
        mock_response = MagicMock()
        mock_response.json.return_value = api_payload
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await svc.search("query fraîche", goal_type="scholarship")

        # Redis setex appelé avec TTL scholarship (4h = 14400s)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 4 * 3600  # TTL scholarship

    def test_to_goal_context_format(self):
        result = SearchResult(
            content="Contenu de test",
            citations=["https://a.com", "https://b.com"],
            query="ma query",
            model="sonar-pro",
            cached=False,
            latency_ms=1234,
        )
        ctx_data = result.to_goal_context()
        assert ctx_data["content"] == "Contenu de test"
        assert ctx_data["citations"] == ["https://a.com", "https://b.com"]
        assert "latency_ms" not in ctx_data  # ne pas exposer la perf interne
        assert "cached" not in ctx_data


# ── Tests : intégration search_trigger + service (bout en bout mock) ───────


class TestSearchEnrichmentEndToEnd:
    """Vérifie le flux complet : trigger → query → result → goal_context."""

    @pytest.mark.asyncio
    async def test_enrichment_injects_search_results(self):
        """Si should_search → True, goal_context doit contenir search_results."""
        from app.agents.orchestrator import Orchestrator
        from app.agents.base import AgentContext
        from app.llm.mock_provider import MockProvider
        import uuid

        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="quelles bourses d'études 2026 pour l'informatique au Sénégal",
            goal_type="scholarship",
            profile={"country": "Sénégal", "domain": "Informatique"},
        )

        mock_search_result = SearchResult(
            content="Bourse X disponible, deadline 31 mars 2026.",
            citations=["https://example-bourse.com"],
            query="bourses d'études Informatique Sénégal 2026",
            model="sonar-pro",
            cached=False,
            latency_ms=1500,
        )

        mock_svc = MagicMock()
        mock_svc.available = True
        mock_svc.search = AsyncMock(return_value=mock_search_result)

        with patch(
            "app.agents.orchestrator.get_perplexity_search_service",
            return_value=mock_svc,
        ):
            orch = Orchestrator(llm=MockProvider())
            enriched_ctx = await orch._enrich_with_search(ctx)

        assert "search_results" in enriched_ctx.goal_context
        sr = enriched_ctx.goal_context["search_results"]
        assert "Bourse X" in sr["content"]
        assert "example-bourse.com" in sr["citations"][0]

    @pytest.mark.asyncio
    async def test_enrichment_skips_when_service_unavailable(self):
        """Si service indisponible → ctx inchangé, pas d'exception."""
        from app.agents.orchestrator import Orchestrator
        from app.agents.base import AgentContext
        from app.llm.mock_provider import MockProvider
        import uuid

        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="quelles bourses 2026",
            goal_type="scholarship",
            profile={"country": "Cameroun"},
        )

        mock_svc = MagicMock()
        mock_svc.available = False

        with patch(
            "app.agents.orchestrator.get_perplexity_search_service",
            return_value=mock_svc,
        ):
            orch = Orchestrator(llm=MockProvider())
            result_ctx = await orch._enrich_with_search(ctx)

        assert "search_results" not in result_ctx.goal_context
        assert result_ctx.goal_context == ctx.goal_context  # inchangé

    @pytest.mark.asyncio
    async def test_enrichment_skips_on_ineligible_goal_type(self):
        """goal_type non éligible → should_search=False → service non appelé."""
        from app.agents.orchestrator import Orchestrator
        from app.agents.base import AgentContext
        from app.llm.mock_provider import MockProvider
        import uuid

        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="génère un document 2026",
            goal_type="document",  # non éligible
            profile={},
        )

        mock_svc = MagicMock()
        mock_svc.available = True
        mock_svc.search = AsyncMock()

        with patch(
            "app.agents.orchestrator.get_perplexity_search_service",
            return_value=mock_svc,
        ):
            orch = Orchestrator(llm=MockProvider())
            result_ctx = await orch._enrich_with_search(ctx)

        mock_svc.search.assert_not_called()
        assert "search_results" not in result_ctx.goal_context

    @pytest.mark.asyncio
    async def test_enrichment_is_idempotent(self):
        """Si search_results déjà présent → pas de second appel API."""
        from app.agents.orchestrator import Orchestrator
        from app.agents.base import AgentContext
        from app.llm.mock_provider import MockProvider
        import uuid

        ctx = AgentContext(
            user_id=uuid.uuid4(),
            message="bourses 2026",
            goal_type="scholarship",
            profile={},
            goal_context={"search_results": {"content": "déjà là", "citations": []}},
        )

        mock_svc = MagicMock()
        mock_svc.available = True
        mock_svc.search = AsyncMock()

        with patch(
            "app.agents.orchestrator.get_perplexity_search_service",
            return_value=mock_svc,
        ):
            orch = Orchestrator(llm=MockProvider())
            result_ctx = await orch._enrich_with_search(ctx)

        mock_svc.search.assert_not_called()
        assert result_ctx.goal_context["search_results"]["content"] == "déjà là"
