"""Perplexity Sonar scraper — intelligence de marché via recherche web IA.

Complète Apify sur les données difficiles à scraper structurellement :
bourses, subventions, appels à candidature, formations, ressources académiques.

Stratégie coût : modèle sonar ($5/1000 req), 14 requêtes/jour ≈ $2-3/mois.
API compatible OpenAI (chat/completions).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.services.scraping.pipeline import (
    embed_pending,
    extract_pending_deadlines,
    normalize_title,
    parse_explicit_deadline,
    process_offer,
)

logger = logging.getLogger(__name__)

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar"

# ── Requêtes quotidiennes ──────────────────────────────────────────────────────

_DAILY_QUERIES: list[dict[str, str]] = [
    # Bourses & formations
    {
        "prompt": "Find the 5 most recent scholarship or fellowship opportunities "
        "for African students and young professionals in 2026. Include real URLs.",
        "offer_type": "formation",
        "region": "Africa",
    },
    {
        "prompt": "Trouve les 5 dernières bourses d'études ou formations gratuites "
        "pour les Africains en 2026 (Erasmus, DAAD, Commonwealth, AFD, UA). "
        "Inclure les vrais liens.",
        "offer_type": "formation",
        "region": "Afrique",
    },
    {
        "prompt": "Find the 5 best free online certification programs "
        "(Coursera, edX, Google, AWS, Meta) available or discounted for Africa "
        "in 2026. Real URLs only.",
        "offer_type": "formation",
        "region": "Africa",
    },
    # Appels à candidature
    {
        "prompt": "Find the 5 most recent calls for applications for African "
        "startups, entrepreneurs or youth in 2026: accelerators, competitions, "
        "pitch contests. Real URLs.",
        "offer_type": "call_for_applications",
        "region": "Africa",
    },
    {
        "prompt": "Trouve les 5 derniers appels à candidature en Afrique "
        "francophone 2026 : concours, incubateurs, programmes entrepreneuriat. "
        "Liens réels.",
        "offer_type": "call_for_applications",
        "region": "Afrique francophone",
    },
    # Subventions & financements
    {
        "prompt": "Find the 5 most recent grants or funding opportunities for "
        "African startups and SMEs in 2026 from international organizations "
        "(World Bank, IFC, AfDB, EU). Real URLs.",
        "offer_type": "grant",
        "region": "Africa",
    },
    {
        "prompt": "Trouve les 5 dernières subventions ou financements disponibles "
        "pour startups et PME africaines en 2026 (Banque Mondiale, AFD, BAD, UE). "
        "Liens réels.",
        "offer_type": "grant",
        "region": "Afrique",
    },
    # Emploi remote
    {
        "prompt": "Find the 5 best remote job opportunities for African "
        "professionals in tech, finance or marketing in 2026 from international "
        "companies hiring in Africa. Real URLs.",
        "offer_type": "job",
        "region": "Africa remote",
    },
    # Entrepreneuriat
    {
        "prompt": "Find the 5 most active entrepreneurship programs or mentorship "
        "initiatives for young African entrepreneurs in 2026. Real URLs.",
        "offer_type": "opportunity",
        "region": "Africa",
    },
    # Ressources
    {
        "prompt": "Find the 5 most useful free resources, toolkits or platforms "
        "launched in 2026 for African professionals and entrepreneurs. Real URLs.",
        "offer_type": "resource",
        "region": "Africa",
    },
    # Contenu académique
    {
        "prompt": "Trouve les 5 meilleures formations ou ressources gratuites "
        "en ligne sur la méthodologie de rédaction de mémoire, rapport de stage "
        "ou thèse en 2026. Liens réels.",
        "offer_type": "formation",
        "region": "Afrique francophone",
    },
    # Annales et examens
    {
        "prompt": "Trouve les 10 meilleures ressources gratuites d'annales, "
        "sujets corrigés, fiches de révision ou plateformes de préparation "
        "aux examens et concours en Afrique et dans le monde francophone. "
        "Liens réels et fonctionnels.",
        "offer_type": "resource",
        "region": "Afrique et monde francophone",
    },
    # Ateliers académiques
    {
        "prompt": "Find the 5 most recent free online workshops, webinars or "
        "courses on academic writing, research methodology or thesis writing "
        "for African students in 2026. Real URLs.",
        "offer_type": "formation",
        "region": "Africa",
    },
    # Concours fonction publique
    {
        "prompt": "Trouve les 10 derniers appels à candidature, concours ouverts "
        "ou programmes de préparation aux examens compétitifs en Afrique "
        "francophone 2026 : fonction publique, grandes écoles, concours médicaux, "
        "examens professionnels. Liens réels.",
        "offer_type": "call_for_applications",
        "region": "Afrique et monde francophone",
    },
]

_SYSTEM_PROMPT = (
    "You are a research assistant specialized in African opportunities.\n"
    "Search the web for real, current opportunities and return them as a JSON array.\n\n"
    "STRICT RULES:\n"
    "1. Return ONLY a valid JSON array — no markdown, no explanation, no code block.\n"
    "2. Each item must have: { \"title\": string, \"description\": string (2-3 sentences), "
    "\"url\": string (real URL from your sources), \"company\": string (organization name), "
    "\"location\": string (country or region), \"deadline\": string (ISO date YYYY-MM-DD) or null }\n"
    "3. Only include opportunities with REAL, VERIFIABLE URLs from your search results.\n"
    "4. Do NOT invent URLs. Use only URLs from your citations.\n"
    "5. Each description must be factual and sourced.\n"
    "6. If fewer than 3 results are found, return what you found (do not pad).\n"
    "7. \"deadline\": set it ONLY if the source page explicitly states an application "
    "deadline / closing date. Never estimate or guess a date. Use null if no explicit "
    "deadline is mentioned, or if it has already passed.\n"
    'Example: [{"title":"...","description":"...","url":"https://...","company":"...",'
    '"location":"Africa","deadline":"2026-08-15"}]'
)

_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")


class PerplexityService:
    """Scraping via Perplexity Sonar — requêtes quotidiennes + on-demand."""

    def __init__(self, db: Session, api_key: str) -> None:
        self.db = db
        self.api_key = api_key

    async def run_daily_queries(self) -> dict[str, int]:
        """Exécute toutes les requêtes quotidiennes. Retourne les stats."""
        stats = {"queried": 0, "extracted": 0, "stored": 0}

        for q in _DAILY_QUERIES:
            try:
                offers = await self._query(q["prompt"], q["offer_type"], q["region"])
                stats["queried"] += 1
                stats["extracted"] += len(offers)
                for offer_data in offers:
                    if self._store(offer_data):
                        stats["stored"] += 1
                await asyncio.sleep(1.5)  # Rate limit
            except Exception:
                logger.warning("Perplexity query failed: %s", q["offer_type"], exc_info=True)

        logger.info(
            "Perplexity daily done: %d queries, %d extracted, %d stored",
            stats["queried"], stats["extracted"], stats["stored"],
        )
        # Vectoriser les offres fraîchement ingérées (no-op si pas de clé Perplexity).
        if stats["stored"]:
            try:
                await embed_pending(self.db, limit=stats["stored"])
            except Exception:
                logger.warning("Embedding indexation skipped after Perplexity daily", exc_info=True)
            # Filet de sécurité : Perplexity renvoie déjà la deadline dans son JSON
            # (cf. _parse_offers) mais peut l'omettre — backfill LLM pour les offres
            # encore sans expires_at (couvre aussi les lignes Perplexity historiques).
            try:
                await extract_pending_deadlines(self.db, limit=stats["stored"])
            except Exception:
                logger.warning("Deadline extraction skipped after Perplexity daily", exc_info=True)
        return stats

    async def run_on_demand(
        self, prompt: str, offer_type: str, region: str
    ) -> list[dict[str, Any]]:
        """Requête à la demande — retourne les offres trouvées."""
        try:
            offers = await self._query(prompt, offer_type, region)
            stored = 0
            for offer_data in offers:
                if self._store(offer_data):
                    stored += 1
            if stored:
                try:
                    await embed_pending(self.db, limit=stored)
                except Exception:
                    logger.warning("Embedding indexation skipped after on-demand", exc_info=True)
                try:
                    await extract_pending_deadlines(self.db, limit=stored)
                except Exception:
                    logger.warning("Deadline extraction skipped after on-demand", exc_info=True)
            return offers
        except Exception:
            logger.warning("Perplexity on-demand query failed", exc_info=True)
            return []

    # ── Privé ──────────────────────────────────────────────────────────────────

    async def _query(
        self, prompt: str, offer_type: str, region: str
    ) -> list[dict[str, Any]]:
        """Appelle Perplexity Sonar et parse le JSON retourné."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                PERPLEXITY_API_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={
                    "model": PERPLEXITY_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"Region: {region}\nType: {offer_type}\n\n{prompt}",
                        },
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.1,
                    "return_citations": True,
                    "search_recency_filter": "month",
                },
            )
            resp.raise_for_status()

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        citations = data.get("citations", [])
        return self._parse_offers(content, citations, offer_type, region)

    def _parse_offers(
        self,
        content: str,
        citations: list[str],
        offer_type: str,
        region: str,
    ) -> list[dict[str, Any]]:
        """Parse le JSON retourné par Perplexity en liste de dicts normalisés."""
        match = _JSON_ARRAY_RE.search(content)
        if not match:
            return []

        try:
            raw_items = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return []

        if not isinstance(raw_items, list):
            return []

        now = datetime.now(timezone.utc)
        available_citations = list(citations)  # copie pour consommation
        results: list[dict[str, Any]] = []

        for item in raw_items[:8]:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue

            url = (item.get("url") or "").strip()
            if url and not url.startswith("http"):
                url = ""
            if not url and available_citations:
                url = available_citations.pop(0)

            external_id = url or f"perplexity-{offer_type}-{now.timestamp()}-{len(results)}"

            results.append({
                "source": "perplexity",
                "offer_type": offer_type,
                "external_id": external_id,
                "title": title,
                "description": (item.get("description") or "")[:2000] or None,
                "url": url or None,
                "company": (item.get("company") or "")[:200] or f"Perplexity/{region}",
                "location": item.get("location") or region,
                "posted_at": now,
                # Perplexity a déjà lu la page source — on lui demande la deadline
                # directement plutôt que de la re-extraire après coup (cf. _SYSTEM_PROMPT).
                "expires_at": parse_explicit_deadline(item.get("deadline")),
            })

        return results

    def _store(self, offer_data: dict[str, Any]) -> bool:
        """Upsert une offre Perplexity en DB. Retourne True si succès."""
        try:
            offer_type = ScrapedOfferType(offer_data["offer_type"])
        except ValueError:
            offer_type = ScrapedOfferType.opportunity

        try:
            from sqlalchemy import select

            stmt = select(ScrapedOffer).where(
                ScrapedOffer.source == offer_data["source"],
                ScrapedOffer.external_id == offer_data["external_id"],
            )
            existing = self.db.execute(stmt).scalar_one_or_none()

            if existing:
                existing.title = offer_data["title"]
                existing.description = offer_data.get("description")
                existing.url = offer_data.get("url")
                existing.company = offer_data.get("company")
                existing.scraped_at = datetime.now(timezone.utc)
                # Ne met à jour que si une nouvelle deadline explicite est trouvée —
                # une requête ultérieure qui ne la redétecte pas ne doit pas effacer
                # une valeur déjà connue (ex: trouvée par extract_pending_deadlines).
                if offer_data.get("expires_at"):
                    existing.expires_at = offer_data["expires_at"]
                self.db.flush()
                process_offer(self.db, existing.id)
                return True

            offer = ScrapedOffer(
                source=offer_data["source"],
                offer_type=offer_type,
                external_id=offer_data["external_id"],
                title=offer_data["title"],
                description=offer_data.get("description"),
                url=offer_data.get("url"),
                company=offer_data.get("company"),
                location=offer_data.get("location"),
                posted_at=offer_data.get("posted_at"),
                expires_at=offer_data.get("expires_at"),
                raw_data={},
            )
            self.db.add(offer)
            self.db.flush()
            process_offer(self.db, offer.id)
            return True
        except Exception:
            logger.debug("Perplexity store skipped (duplicate?)", exc_info=True)
            return False
