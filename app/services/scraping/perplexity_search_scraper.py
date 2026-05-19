"""PerplexitySearchScraper — scraping via l'endpoint /search de Perplexity.

Différence fondamentale avec PerplexityService (chat/completions) :
- Utilise /search → résultats web bruts (title, url, snippet, date)
- Aucune synthèse LLM → zéro hallucination d'URLs, données 100% vérifiables
- 10 résultats réels par requête

Rôle dans le pipeline global :
    Apify                   → volume emplois structurés (Indeed, LinkedIn)     daily
    PerplexityService       → analyse qualitative via chat/completions          2x/jour
    PerplexitySearchScraper → résultats web bruts, rotation géographique        2x/jour

Rotation géographique (2 runs/jour) :
    06h UTC → run_africa()  : Afrique, Afrique francophone, sub-Saharan Africa
    18h UTC → run_global()  : International, mondial, programmes ouverts à tous

Précision des données — deux mécanismes complémentaires :

  1. Détection de pages listing (_is_listing_page) :
     Les moteurs de recherche remontent souvent des agrégateurs ("15 bourses",
     "Top 10 grants") plutôt que des pages d'opportunité directes.
     On détecte ces pages par pattern de titre et on les traite différemment.

  2. Extraction de second niveau (_extract_items_from_listing) :
     Pour les pages listing détectées, on appelle chat/completions pour extraire
     les opportunités individuelles mentionnées dans le snippet.
     Résultat : 1 article "15 bourses" → 3-5 fiches individuelles avec titres précis.
     Ces fiches sont stockées avec source="perplexity_extracted", référence au parent.
     Leurs embeddings sont plus précis car le texte est plus spécifique.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.services.scraping.pipeline import embed_pending, process_offer

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.perplexity.ai/search"
_CHAT_URL   = "https://api.perplexity.ai/chat/completions"
_HTTP_TIMEOUT = 20.0
_SLEEP_BETWEEN_QUERIES = 1.5
_MAX_RESULTS_PER_QUERY = 10
_MAX_TOKENS_PER_PAGE = 512

# ── Détection de pages listing ─────────────────────────────────────────────────
#
# Ces patterns identifient des articles agrégateurs plutôt que des pages
# d'opportunité directes. On ne les supprime pas — on les enrichit via LLM.

_LISTING_TITLE_RE = re.compile(
    r"""
    (?:
        # Nombre suivi d'un mot de catégorie : "15 bourses", "10 grants"
        \d+\s+(?:bourses?|grants?|scholarships?|programs?|opportunités?
                 |financements?|formations?|emplois?|offres?|subventions?
                 |ressources?|conseils?|étapes?)
    |   top\s+\d+               # "Top 10"
    |   meilleures?\s+\d+       # "Meilleures 5"
    |   liste\s+d[eu]s?\s+      # "liste des", "liste de"
    |   guide\s+complet         # "guide complet"
    |   tout\s+ce\s+que         # "tout ce que vous devez"
    |   comment\s+trouver       # "comment trouver"
    |   où\s+trouver            # "où trouver"
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_LISTING_URL_RE = re.compile(
    r"/(blog|article|news|actualit|list|guide|top-\d|categor)/",
    re.IGNORECASE,
)


def _is_listing_page(title: str, url: str) -> bool:
    """Retourne True si ce résultat est probablement une page agrégateur."""
    if _LISTING_TITLE_RE.search(title):
        return True
    if _LISTING_URL_RE.search(url):
        return True
    return False


# ── Extraction LLM depuis les pages listing ────────────────────────────────────

_EXTRACTION_SYSTEM = (
    "Tu es un assistant spécialisé dans l'extraction d'opportunités. "
    "À partir du titre et du résumé d'un article web, tu extrais les "
    "opportunités individuelles mentionnées.\n\n"
    "RÈGLES STRICTES :\n"
    "1. Retourne UNIQUEMENT un tableau JSON valide — aucun texte autour.\n"
    "2. Chaque item : {\"title\": string, \"description\": string (1-2 phrases), "
    "\"organization\": string ou null}\n"
    "3. Maximum 5 items. Seulement les opportunités clairement identifiables.\n"
    "4. Ne pas inventer de détails absents du texte.\n"
    "5. Si le texte ne permet pas d'extraire d'items précis, retourner [].\n"
    "Exemple: [{\"title\":\"Bourse DAAD 2026\","
    "\"description\":\"Bourse pour masters en Allemagne, deadline mars 2026.\","
    "\"organization\":\"DAAD\"}]"
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _parse_extracted(raw: str) -> list[dict]:
    """Parse la réponse JSON du LLM d'extraction."""
    # Nettoyer les blocs markdown éventuels
    m = _FENCE_RE.search(raw)
    text = m.group(1).strip() if m else raw.strip()

    # Trouver le tableau JSON
    start = text.find("[")
    end = text.rfind("]") + 1
    if start == -1 or end == 0:
        return []
    try:
        items = json.loads(text[start:end])
        return [i for i in items if isinstance(i, dict) and i.get("title")]
    except (json.JSONDecodeError, ValueError):
        return []

# ── Extraction de localisation depuis la query ────────────────────────────────
# Ordre important : du plus spécifique au plus général.
_LOCATION_PATTERNS: list[tuple[str, str]] = [
    ("afrique de l'ouest",     "Afrique de l'Ouest"),
    ("afrique de l est",       "Afrique de l'Est"),
    ("afrique centrale",       "Afrique centrale"),
    ("afrique australe",       "Afrique australe"),
    ("afrique subsaharienne",  "Afrique subsaharienne"),
    ("sub-saharan africa",     "Sub-Saharan Africa"),
    ("afrique francophone",    "Afrique francophone"),
    ("francophone",            "Afrique francophone"),
    ("west africa",            "West Africa"),
    ("east africa",            "East Africa"),
    ("afrique",                "Afrique"),
    ("africa",                 "Africa"),
    ("worldwide",              "International"),
    ("global",                 "International"),
    ("international",         "International"),
]


def _extract_location(query: str, default: str) -> str:
    """Dérive la localisation la plus précise depuis les mots-clés de la requête."""
    q = query.lower()
    for keyword, location in _LOCATION_PATTERNS:
        if keyword in q:
            return location
    return default


# ── CATALOGUE AFRIQUE — run 06h UTC ──────────────────────────────────────────
#
# Focus : Afrique, Afrique francophone, sub-Saharan Africa, Afrique de l'Ouest,
#         Afrique de l'Est, Afrique centrale, Afrique australe.
# Langue : FR dominante + EN pour les programmes anglophones.

QUERIES_AFRICA: list[dict[str, str]] = [

    # Bourses & études
    {
        "query": "bourses études Afrique 2026 candidature ouverte date limite inscription",
        "offer_type": "scholarship",
    },
    {
        "query": "bourses master doctorat Afrique francophone 2026 universités programmes",
        "offer_type": "scholarship",
    },
    {
        "query": "bourse excellence sciences ingénierie technologie Afrique 2026",
        "offer_type": "scholarship",
    },
    {
        "query": "bourses coopération internationale Afrique francophone 2026 appel candidature",
        "offer_type": "scholarship",
    },
    {
        "query": "scholarship fellowship program Africa 2026 apply funded",
        "offer_type": "scholarship",
    },
    {
        "query": "bourse formation professionnelle jeunes Afrique subsaharienne 2026",
        "offer_type": "scholarship",
    },
    {
        "query": "scholarship African students sub-Saharan Africa 2026 open",
        "offer_type": "scholarship",
    },
    {
        "query": "bourses gouvernements Afrique de l'Ouest Afrique centrale 2026",
        "offer_type": "scholarship",
    },

    # Emplois & recrutement
    {
        "query": "offres emploi tech startup fintech Afrique francophone recrutement 2026",
        "offer_type": "job",
    },
    {
        "query": "emploi ONG organisation internationale Afrique de l'Ouest 2026",
        "offer_type": "job",
    },
    {
        "query": "recrutement Africa développement secteur privé public 2026",
        "offer_type": "job",
    },
    {
        "query": "offres emploi agritech santé éducation Afrique francophone 2026",
        "offer_type": "job",
    },
    {
        "query": "jobs Africa development NGO international organizations hiring 2026",
        "offer_type": "job",
    },
    {
        "query": "stages rémunérés entreprises organisations Afrique 2026 candidature",
        "offer_type": "job",
    },
    {
        "query": "recrutement function publique concours Afrique francophone 2026",
        "offer_type": "job",
    },

    # Subventions & financements
    {
        "query": "subventions financement startups PME Afrique 2026 appel dossier",
        "offer_type": "grant",
    },
    {
        "query": "financement banques développement institutions financières projets Afrique 2026",
        "offer_type": "grant",
    },
    {
        "query": "financement coopération bilatérale multilatérale Afrique 2026",
        "offer_type": "grant",
    },
    {
        "query": "financement agriculture agritech énergie solaire Afrique 2026",
        "offer_type": "grant",
    },
    {
        "query": "fonds investissement impact social entrepreneuriat Afrique 2026",
        "offer_type": "grant",
    },
    {
        "query": "grants funding African startups entrepreneurs SMEs 2026",
        "offer_type": "grant",
    },
    {
        "query": "financement femmes entrepreneuriat Afrique subsaharienne 2026",
        "offer_type": "grant",
    },

    # Appels à candidature & concours
    {
        "query": "appel candidature incubateur accélérateur Afrique 2026",
        "offer_type": "call_for_applications",
    },
    {
        "query": "concours entrepreneuriat innovation prix Afrique francophone 2026",
        "offer_type": "call_for_applications",
    },
    {
        "query": "programme leadership jeunesse africaine 2026 candidature ouvert",
        "offer_type": "call_for_applications",
    },
    {
        "query": "hackathon challenge numérique innovation Afrique 2026",
        "offer_type": "call_for_applications",
    },
    {
        "query": "appels offres marchés publics organisations Afrique 2026",
        "offer_type": "call_for_applications",
    },
    {
        "query": "concours fonction publique grandes écoles Afrique francophone 2026",
        "offer_type": "call_for_applications",
    },

    # Formations & certifications
    {
        "query": "formation gratuite certification professionnelle Afrique 2026 en ligne",
        "offer_type": "formation",
    },
    {
        "query": "Google Microsoft AWS Meta formation Afrique francophone 2026 gratuit",
        "offer_type": "formation",
    },
    {
        "query": "bootcamp développement web mobile Afrique formation 2026",
        "offer_type": "formation",
    },
    {
        "query": "formation entrepreneuriat digital marketing Afrique 2026",
        "offer_type": "formation",
    },
    {
        "query": "free online training Africa youth professional development 2026",
        "offer_type": "formation",
    },

    # Opportunités & partenariats
    {
        "query": "opportunités développement professionnel jeunes Afrique 2026",
        "offer_type": "opportunity",
    },
    {
        "query": "Africa tech ecosystem incubateurs startups opportunities 2026",
        "offer_type": "opportunity",
    },
    {
        "query": "partenariat coopération internationale Afrique francophone projets 2026",
        "offer_type": "partnership",
    },
    {
        "query": "programme échange jeunes Afrique international fellowship 2026",
        "offer_type": "opportunity",
    },
    {
        "query": "prix innovation sociale entrepreneuriat Afrique subsaharienne 2026",
        "offer_type": "opportunity",
    },
]


# ── CATALOGUE MONDIAL — run 18h UTC ──────────────────────────────────────────
#
# Focus : International, mondial, programmes sans restriction géographique,
#         institutions globales (ONU, Banque mondiale, UE, grandes fondations),
#         opportunités remote accessibles depuis l'Afrique.
# Langue : EN dominante + FR pour les institutions francophones.

QUERIES_GLOBAL: list[dict[str, str]] = [

    # Bourses & études
    {
        "query": "international scholarships 2026 open applications worldwide deadline",
        "offer_type": "scholarship",
    },
    {
        "query": "global fellowship programs 2026 emerging countries apply now",
        "offer_type": "scholarship",
    },
    {
        "query": "international scholarships exchange programs government funded 2026 open",
        "offer_type": "scholarship",
    },
    {
        "query": "scholarships international higher education exchange students 2026 apply",
        "offer_type": "scholarship",
    },
    {
        "query": "PhD master scholarships developing countries 2026 fully funded",
        "offer_type": "scholarship",
    },
    {
        "query": "foundation endowment scholarships global developing countries 2026",
        "offer_type": "scholarship",
    },
    {
        "query": "bourses études internationales francophones monde 2026 candidature ouverte",
        "offer_type": "scholarship",
    },
    {
        "query": "scholarship STEM engineering medicine global 2026 full funding",
        "offer_type": "scholarship",
    },

    # Emplois & recrutement
    {
        "query": "remote jobs worldwide 2026 open hiring international professionals",
        "offer_type": "job",
    },
    {
        "query": "UN United Nations jobs vacancies 2026 international recruitment",
        "offer_type": "job",
    },
    {
        "query": "World Bank IMF international organizations jobs 2026",
        "offer_type": "job",
    },
    {
        "query": "international NGO nonprofit humanitarian jobs 2026 worldwide",
        "offer_type": "job",
    },
    {
        "query": "remote tech developer designer jobs 2026 global companies hiring",
        "offer_type": "job",
    },
    {
        "query": "emploi télétravail international organismes multilatéraux 2026",
        "offer_type": "job",
    },
    {
        "query": "paid internship international organizations 2026 global apply",
        "offer_type": "job",
    },

    # Subventions & financements
    {
        "query": "World Bank IFC grants funding entrepreneurs developing countries 2026",
        "offer_type": "grant",
    },
    {
        "query": "EU European Union grants international development programs 2026",
        "offer_type": "grant",
    },
    {
        "query": "bilateral development aid funding programs global 2026 apply",
        "offer_type": "grant",
    },
    {
        "query": "climate change green energy funding global 2026 apply",
        "offer_type": "grant",
    },
    {
        "query": "international foundations grants social enterprise global 2026",
        "offer_type": "grant",
    },
    {
        "query": "startup funding global accelerators venture capital emerging markets 2026",
        "offer_type": "grant",
    },
    {
        "query": "financement international coopération développement durable 2026",
        "offer_type": "grant",
    },

    # Appels à candidature & concours
    {
        "query": "global startup competition awards 2026 apply prize funding",
        "offer_type": "call_for_applications",
    },
    {
        "query": "international call for applications fellowship program 2026",
        "offer_type": "call_for_applications",
    },
    {
        "query": "innovation challenge contest global entrepreneurs 2026",
        "offer_type": "call_for_applications",
    },
    {
        "query": "United Nations programs youth leadership global 2026",
        "offer_type": "call_for_applications",
    },
    {
        "query": "international tender procurement consultancy 2026 open",
        "offer_type": "call_for_applications",
    },
    {
        "query": "appel candidature programme mondial jeunesse ONU UNESCO 2026",
        "offer_type": "call_for_applications",
    },

    # Formations & certifications
    {
        "query": "free online courses certifications 2026 worldwide open enrollment",
        "offer_type": "formation",
    },
    {
        "query": "professional certification programs 2026 online global",
        "offer_type": "formation",
    },
    {
        "query": "global online workshops webinars skills 2026 free registration",
        "offer_type": "formation",
    },
    {
        "query": "international training programs 2026 scholarship funded",
        "offer_type": "formation",
    },
    {
        "query": "MOOC formation professionnelle internationale francophone 2026",
        "offer_type": "formation",
    },

    # Opportunités & partenariats
    {
        "query": "global opportunities young professionals emerging markets 2026",
        "offer_type": "opportunity",
    },
    {
        "query": "international partnership cooperation development projects 2026",
        "offer_type": "partnership",
    },
    {
        "query": "tech entrepreneurship global programs mentorship 2026",
        "offer_type": "opportunity",
    },
    {
        "query": "social entrepreneurship global awards recognition 2026",
        "offer_type": "opportunity",
    },
    {
        "query": "international exchange program cultural professional 2026",
        "offer_type": "opportunity",
    },
]


# ── Service ───────────────────────────────────────────────────────────────────


class PerplexitySearchScraper:
    """Scraping via l'endpoint /search de Perplexity.

    Deux modes d'exécution, appelés séparément par le scheduler :
      run_africa()  → 06h UTC — requêtes Afrique/Afrique francophone
      run_global()  → 18h UTC — requêtes internationales/mondiales
    """

    def __init__(self, db: Session, api_key: str) -> None:
        self.db = db
        self.api_key = api_key

    async def run_africa(self) -> dict[str, int]:
        """Run 06h — catalogue Afrique."""
        return await self._run(QUERIES_AFRICA, label="africa", default_location="Afrique")

    async def run_global(self) -> dict[str, int]:
        """Run 18h — catalogue mondial."""
        return await self._run(QUERIES_GLOBAL, label="global", default_location="International")

    async def _run(
        self, queries: list[dict[str, str]], label: str, default_location: str
    ) -> dict[str, int]:
        """Exécute un catalogue de requêtes et retourne les statistiques."""
        stats = {"queries": 0, "results": 0, "stored": 0, "extracted": 0, "errors": 0}

        for q in queries:
            try:
                location = _extract_location(q["query"], default_location)
                results = await self._search(q["query"])
                stats["queries"] += 1
                stats["results"] += len(results)

                for item in results:
                    item["location"] = location
                    if self._upsert(item, q["offer_type"]):
                        stats["stored"] += 1

                    # Second niveau : extraire les items individuels des pages listing
                    if _is_listing_page(item["title"], item["url"]):
                        extracted = await self._extract_items_from_listing(
                            title=item["title"],
                            url=item["url"],
                            snippet=item.get("description") or "",
                            offer_type=q["offer_type"],
                            location=location,
                        )
                        for ext in extracted:
                            if self._upsert(ext, q["offer_type"]):
                                stats["extracted"] += 1
                        if extracted:
                            await asyncio.sleep(0.5)

                await asyncio.sleep(_SLEEP_BETWEEN_QUERIES)

            except Exception:
                stats["errors"] += 1
                logger.warning(
                    "[PerplexitySearch/%s] query failed: %.60s",
                    label, q["query"], exc_info=True,
                )

        logger.info(
            "[PerplexitySearch/%s] done — queries=%d results=%d stored=%d "
            "extracted_items=%d errors=%d",
            label, stats["queries"], stats["results"], stats["stored"],
            stats["extracted"], stats["errors"],
        )

        if stats["stored"]:
            try:
                indexed = await embed_pending(self.db, limit=stats["stored"] + 20)
                logger.info(
                    "[PerplexitySearch/%s] %d embeddings générés", label, indexed
                )
            except Exception:
                logger.warning(
                    "[PerplexitySearch/%s] embedding skipped", label, exc_info=True
                )

        return stats

    async def _extract_items_from_listing(
        self,
        title: str,
        url: str,
        snippet: str,
        offer_type: str,
        location: str,
    ) -> list[dict[str, Any]]:
        """Extrait les opportunités individuelles d'une page listing via LLM.

        Appelle chat/completions avec le titre + snippet pour obtenir des
        fiches individuelles plus précises. Coût : ~1 appel LLM par page listing.
        Les items extraits sont stockés avec source="perplexity_extracted" et
        une référence URL vers la page parente dans raw_data.
        """
        if not snippet:
            return []

        user_content = (
            f"Article : {title}\n"
            f"URL source : {url}\n"
            f"Résumé : {snippet}\n\n"
            "Extrais les opportunités individuelles mentionnées dans ce texte."
        )

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(
                    _CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "sonar",
                        "messages": [
                            {"role": "system", "content": _EXTRACTION_SYSTEM},
                            {"role": "user", "content": user_content},
                        ],
                        "max_tokens": 600,
                        "temperature": 0.1,
                    },
                )
                resp.raise_for_status()

            raw_text = resp.json()["choices"][0]["message"]["content"]
            items = _parse_extracted(raw_text)

        except Exception:
            logger.debug(
                "[PerplexitySearch] extraction LLM failed for: %s", url, exc_info=True
            )
            return []

        now = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []
        for item in items[:5]:
            item_title = item.get("title", "").strip()
            if not item_title:
                continue
            org = (item.get("organization") or "").strip()
            desc = (item.get("description") or "").strip()
            # Description enrichie = description + nom organisation
            full_desc = f"{desc} [{org}]" if org else desc

            results.append({
                "title": item_title[:500],
                "url": url,               # URL de la page parente (la listing)
                "description": full_desc[:2000] or None,
                "location": location,
                "posted_at": now,
                # Marquer comme item extrait (pas page directe)
                "_source_override": "perplexity_extracted",
                "_parent_url": url,
            })

        return results

    async def _search(self, query: str) -> list[dict[str, Any]]:
        """Appelle /search et retourne les résultats normalisés."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "max_results": _MAX_RESULTS_PER_QUERY,
                    "max_tokens_per_page": _MAX_TOKENS_PER_PAGE,
                },
            )
            resp.raise_for_status()

        raw_results = resp.json().get("results", resp.json().get("data", []))

        normalized: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url or not url.startswith("http"):
                continue

            snippet = (item.get("snippet") or "").strip()
            date_raw = item.get("date") or item.get("last_updated")
            posted_at: datetime | None = None
            if date_raw:
                try:
                    posted_at = datetime.fromisoformat(
                        str(date_raw).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            normalized.append({
                "title": title[:500],
                "url": url,
                "description": snippet[:2000] if snippet else None,
                "posted_at": posted_at,
            })

        return normalized

    def _upsert(self, item: dict[str, Any], offer_type_str: str) -> bool:
        """Upsert un résultat en DB.

        Clé de déduplication :
        - Pages directes  (source=perplexity_search)    : URL
        - Items extraits  (source=perplexity_extracted) : title[:80] + parent_url
          (même URL parente mais titres différents → enregistrements distincts)
        """
        try:
            offer_type = ScrapedOfferType(offer_type_str)
        except ValueError:
            offer_type = ScrapedOfferType.opportunity

        # Déterminer la source et la clé de déduplication
        source = item.pop("_source_override", "perplexity_search")
        parent_url = item.pop("_parent_url", None)
        url = item["url"]

        if source == "perplexity_extracted":
            # Clé = titre normalisé + URL parente pour éviter doublons inter-articles
            title_key = item["title"][:80].lower().strip()
            external_id = f"ext:{hash(title_key + (parent_url or url)) & 0xFFFFFFFF}"
        else:
            external_id = url

        raw_data: dict = {"source": source}
        if parent_url:
            raw_data["parent_url"] = parent_url

        try:
            stmt = select(ScrapedOffer).where(
                ScrapedOffer.source == source,
                ScrapedOffer.external_id == external_id,
            )
            existing = self.db.execute(stmt).scalar_one_or_none()

            if existing:
                existing.title = item["title"]
                if item.get("description"):
                    existing.description = item["description"]
                if item.get("location"):
                    existing.location = item["location"]
                existing.scraped_at = datetime.now(timezone.utc)
                existing.embedding = None
                self.db.flush()
                process_offer(self.db, existing.id)
                return True

            offer = ScrapedOffer(
                source=source,
                offer_type=offer_type,
                external_id=external_id,
                title=item["title"],
                description=item.get("description"),
                url=url,
                location=item.get("location"),
                posted_at=item.get("posted_at"),
                raw_data=raw_data,
            )
            self.db.add(offer)
            self.db.flush()
            process_offer(self.db, offer.id)
            return True

        except Exception:
            logger.debug(
                "[PerplexitySearch] upsert skipped: %s", external_id, exc_info=True
            )
            return False
