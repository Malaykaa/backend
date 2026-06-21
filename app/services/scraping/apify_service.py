"""Apify scraper — Indeed, LinkedIn, Google Jobs, Facebook, sites web.

Architecture : la config définit quels actors lancer et comment normaliser.
run_actor() est la boucle commune (appel actor → normalise → upsert → pipeline).
run_light() et run_heavy() orchestrent les runs selon la fréquence.

Actors utilisés :
  valig/indeed-jobs-scraper              — offres d'emploi Indeed
  curious_coder/linkedin-jobs-scraper    — offres d'emploi LinkedIn
  harvestapi/linkedin-post-search        — posts LinkedIn par hashtag/mot-clé
  apimaestro/linkedin-profile-posts      — posts de pages/profils LinkedIn
  johnvc/Google-Jobs-Scraper             — offres Google Jobs
  apify/facebook-posts-scraper           — posts Facebook
  apify/website-content-crawler          — sites web (offres, bourses, subventions)

Toutes les instructions (pays, mots-clés, URLs, limites) viennent de ce fichier.
Les valeurs par défaut des actors Apify ne s'appliquent que si un paramètre
est absent de input_data — on contrôle tout ici.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.services.scraping.pipeline import embed_pending, extract_pending_deadlines, process_offer

logger = logging.getLogger(__name__)

# ── Actors Apify ──────────────────────────────────────────────────────────────

ACTOR_INDEED          = "valig/indeed-jobs-scraper"
ACTOR_LINKEDIN_JOBS   = "curious_coder/linkedin-jobs-scraper"
ACTOR_LINKEDIN_POSTS  = "harvestapi/linkedin-post-search"
ACTOR_LINKEDIN_PAGES  = "apimaestro/linkedin-profile-posts"
ACTOR_GOOGLE_JOBS     = "johnvc/Google-Jobs-Scraper"
ACTOR_FACEBOOK        = "apify/facebook-posts-scraper"
ACTOR_WEB             = "apify/website-content-crawler"

# ── Pays cibles ───────────────────────────────────────────────────────────────

# Indeed ne supporte qu'un sous-ensemble de pays (codes validés par l'actor).
# Seuls ces 4 pays africains sont dans sa liste d'autorisés.
INDEED_COUNTRIES = [
    {"country": "ng", "location": "Nigeria"},
    {"country": "ma", "location": "Morocco"},
    {"country": "eg", "location": "Egypt"},
    {"country": "za", "location": "South Africa"},
]

# Google Jobs couvre TOUS les pays africains de notre liste — y compris les 17
# rejetés par Indeed (ci, sn, cm, cd, td, tg, ke, rw, gh, ug, tz, et, ml, bf,
# cf, ga, cg) + les 4 couverts par Indeed (ng, ma, eg, za) pour plus de volume.
# On cible la capitale économique principale de chaque pays.
GOOGLE_JOBS_LOCATIONS = [
    "Abidjan, Côte d'Ivoire",        # ci — rejeté Indeed
    "Dakar, Senegal",                 # sn — rejeté Indeed
    "Douala, Cameroon",               # cm — rejeté Indeed
    "Kinshasa, DR Congo",             # cd — rejeté Indeed
    "N'Djamena, Chad",                # td — rejeté Indeed
    "Lomé, Togo",                     # tg — rejeté Indeed
    "Nairobi, Kenya",                 # ke — rejeté Indeed
    "Kigali, Rwanda",                 # rw — rejeté Indeed
    "Accra, Ghana",                   # gh — rejeté Indeed
    "Kampala, Uganda",                # ug — rejeté Indeed
    "Dar es Salaam, Tanzania",        # tz — rejeté Indeed
    "Addis Ababa, Ethiopia",          # et — rejeté Indeed
    "Bamako, Mali",                   # ml — rejeté Indeed
    "Ouagadougou, Burkina Faso",      # bf — rejeté Indeed
    "Bangui, Central African Republic", # cf — rejeté Indeed
    "Libreville, Gabon",              # ga — rejeté Indeed
    "Brazzaville, Republic of the Congo", # cg — rejeté Indeed
    "Lagos, Nigeria",                 # ng — aussi sur Indeed
    "Casablanca, Morocco",            # ma — aussi sur Indeed
    "Cairo, Egypt",                   # eg — aussi sur Indeed
    "Johannesburg, South Africa",     # za — aussi sur Indeed
]

# ── LinkedIn Jobs ─────────────────────────────────────────────────────────────

LINKEDIN_HUBS = [
    "Côte d'Ivoire", "Senegal", "Nigeria", "Kenya", "Morocco",
    "Ghana", "Cameroon", "Egypt", "Rwanda", "South Africa",
]

LINKEDIN_KEYWORDS = [
    "job opportunity Africa",
    "emploi Afrique",
    "bourse étude Afrique",
    "stage Afrique",
    "formation gratuite Afrique",
    "startup Africa funding",
]

# ── LinkedIn Posts (harvestapi/linkedin-post-search) ──────────────────────────
# Recherche par hashtags/mots-clés — postes publics récents

LINKEDIN_POST_SEARCHES = [
    {"keywords": "#emploiAfrique OR #africajobs OR #jobsafrica",          "offer_type": "job"},
    {"keywords": "#bourseAfrique OR #scholarshipafrica OR #africastudy",  "offer_type": "formation"},
    {"keywords": "#subventionAfrique OR #grantafrica OR #financementPME", "offer_type": "grant"},
    {"keywords": "#africastartup OR #entrepreneurafrique OR #appelacandidature", "offer_type": "call_for_applications"},
    {"keywords": "#opportuniteAfrique OR #recrutementAfrique",            "offer_type": "opportunity"},
]

# ── LinkedIn Pages entreprises (apimaestro/linkedin-profile-posts) ────────────
# Pages d'organisations actives sur les opportunités en Afrique

LINKEDIN_COMPANY_PAGES = [
    {"url": "https://www.linkedin.com/company/african-development-bank-group/", "offer_type": "grant"},
    {"url": "https://www.linkedin.com/company/myjobmag/",                       "offer_type": "job"},
    {"url": "https://www.linkedin.com/company/jobberman/",                       "offer_type": "job"},
    {"url": "https://www.linkedin.com/company/opportunity-youth-africa/",        "offer_type": "opportunity"},
    {"url": "https://www.linkedin.com/company/yali-network/",                   "offer_type": "opportunity"},
    {"url": "https://www.linkedin.com/company/brightermonday/",                 "offer_type": "job"},
]

# ── Google Jobs ───────────────────────────────────────────────────────────────
# Requêtes par pays/ville pour les runs light et heavy

GOOGLE_JOBS_QUERIES = [
    {"query": "emploi",      "offer_type": "job"},
    {"query": "jobs hiring", "offer_type": "job"},
    {"query": "stage internship Africa", "offer_type": "job"},
    {"query": "scholarship bourse Africa 2025", "offer_type": "formation"},
    {"query": "grant funding Africa startup", "offer_type": "grant"},
]

# ── Facebook ──────────────────────────────────────────────────────────────────
# Les URLs de recherche Facebook sont bloquées. On cible des pages publiques
# actives sur les emplois/opportunités en Afrique.
# Si une page n'existe pas ou retourne 0 résultats, l'actor passe sans planter.

FACEBOOK_PAGES = [
    # ── Job boards africains ───────────────────────────────────────────────
    {"url": "https://www.facebook.com/myjobmag",            "offer_type": "job"},
    {"url": "https://www.facebook.com/jobberman",           "offer_type": "job"},
    {"url": "https://www.facebook.com/brightermonday",      "offer_type": "job"},
    {"url": "https://www.facebook.com/rekrute.ma",          "offer_type": "job"},
    # ── Opportunités & bourses ─────────────────────────────────────────────
    {"url": "https://www.facebook.com/afterschoolafrica",   "offer_type": "formation"},
    {"url": "https://www.facebook.com/scholars4dev",        "offer_type": "formation"},
    {"url": "https://www.facebook.com/AfricaOpportunities", "offer_type": "opportunity"},
    {"url": "https://www.facebook.com/youthopportunities",  "offer_type": "formation"},
    # ── Entrepreneuriat & subventions ──────────────────────────────────────
    {"url": "https://www.facebook.com/yalinetwork",         "offer_type": "opportunity"},
    {"url": "https://www.facebook.com/tonyelumelu",         "offer_type": "grant"},
]

# ── Sites web ─────────────────────────────────────────────────────────────────

WEB_URLS = {
    "opportunities": [
        "https://www.youthop.com/",
        "https://opportunitiesforyouth.org/",
        "https://www.afterschoolafrica.com/",
        "https://servicepublic.gouv.ci/",          # CI — services publics & concours
        "https://projets.agenceemploijeunes.ci/",  # CI — agence emploi jeunes
    ],
    "job_boards": [
        "https://myjobmag.com/jobs",               # URL correcte (pas /jobs/africa)
        "https://www.jobberman.com/jobs",
        "https://www.brightermonday.com/jobs",
        "https://www.careers24.com/jobs/",
        "https://www.rekrute.com/offres-emploi.html",
        "https://wiijob.com/offres-emploi/",           # CI — wiijob
        "https://www.jobivoire.ci/job",                # CI — jobivoire
        "https://www.emploi.ci/recherche-jobs-cote-ivoire", # CI — emploi.ci
        # novojob.com retiré — domaine mort (code 000)
        "https://www.goafricaonline.com/ci/emploi/",  # CI — GoAfrica
    ],
    "grants": [
        # afdb.org retiré — bloque les crawlers (403)
        "https://yali.state.gov/",
        "https://www.opportunitiesforafricans.com/",
        "https://africagrants.com/",
    ],
    "scholarships": [
        "https://scholarshipdb.net/scholarships-in/Africa",
        "https://scholars4dev.com/",
        "https://www.afterschoolafrica.com/scholarships/",
        "https://www.lelivretdesconcours.com/",        # CI — concours & examens
    ],
}


# ── Normalizers ───────────────────────────────────────────────────────────────

NormalizedDict = dict[str, Any]
Normalizer = Callable[[Any, str, str], NormalizedDict | None]


def _safe_str(val: Any, default: str = "") -> str:
    return str(val).strip() if val else default


def _safe_date(val: Any) -> datetime | None:
    if not val:
        return None
    try:
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val, tz=timezone.utc)
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except (ValueError, TypeError, OSError):
        return None


def normalize_indeed(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    key = _safe_str(rec.get("key") or rec.get("jobKey") or rec.get("url"))
    title = _safe_str(rec.get("title"))
    if not key and not title:
        return None
    url = _safe_str(rec.get("url") or rec.get("jobUrl"))
    employer = rec.get("employer") or {}
    company = _safe_str(employer.get("name") if isinstance(employer, dict) else None)
    loc = rec.get("location") or {}
    location = ", ".join(filter(None, [
        _safe_str(loc.get("city") if isinstance(loc, dict) else None),
        _safe_str(loc.get("countryName") if isinstance(loc, dict) else None),
    ])) or None
    return {
        "source": source, "offer_type": offer_type,
        "external_id": key or url or f"indeed-{id(item)}",
        "title": title, "description": _safe_str(rec.get("description"))[:2000] or None,
        "url": url or None, "company": company or None, "location": location,
        "salary": _safe_str(rec.get("salary")) or None,
        "posted_at": _safe_date(rec.get("datePublished")),
        "expires_at": _safe_date(rec.get("expirationDate")),
        "raw_data": rec,
    }


def normalize_linkedin_job(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    title = _safe_str(rec.get("title") or rec.get("jobTitle") or rec.get("positionTitle"))
    if not title:
        return None
    url = _safe_str(rec.get("url") or rec.get("jobUrl") or rec.get("link"))
    company_raw = rec.get("company") or rec.get("companyName")
    company = _safe_str(company_raw.get("name") if isinstance(company_raw, dict) else company_raw)
    return {
        "source": source, "offer_type": offer_type,
        "external_id": url or f"linkedin-{id(item)}",
        "title": title, "description": _safe_str(rec.get("description"))[:2000] or None,
        "url": url or None, "company": company or None,
        "location": _safe_str(rec.get("location") or rec.get("locationName")) or None,
        "salary": _safe_str(rec.get("salary")) or None,
        "posted_at": _safe_date(rec.get("postedAt") or rec.get("datePosted") or rec.get("listedAt")),
        "raw_data": rec,
    }


def normalize_linkedin_post(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    """Normalise un post LinkedIn (harvestapi ou apimaestro)."""
    rec = item if isinstance(item, dict) else {}
    text = _safe_str(rec.get("text") or rec.get("content") or rec.get("commentary"))
    url = _safe_str(rec.get("url") or rec.get("postUrl") or rec.get("shareUrl"))
    post_id = _safe_str(rec.get("id") or rec.get("postId") or rec.get("urn"))
    if not text and not url:
        return None
    author = rec.get("author") or rec.get("actorName") or {}
    author_name = _safe_str(author.get("name") if isinstance(author, dict) else author)
    return {
        "source": source, "offer_type": offer_type,
        "external_id": post_id or url or f"li-post-{id(item)}",
        "title": text[:120] or "Post LinkedIn",
        "description": text[:2000] or None,
        "url": url or None,
        "company": author_name or None,
        "location": "Africa",
        "posted_at": _safe_date(rec.get("postedAt") or rec.get("createdAt") or rec.get("date")),
        "raw_data": rec,
    }


def normalize_google_jobs(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    # Champs confirmés par les logs Apify : title, company_name, location,
    # share_link, source_link, description, job_id, job_title
    rec = item if isinstance(item, dict) else {}
    title = _safe_str(rec.get("title") or rec.get("job_title"))
    if not title:
        return None
    url = _safe_str(rec.get("share_link") or rec.get("source_link"))
    company = _safe_str(rec.get("company_name") or rec.get("company"))
    location = _safe_str(rec.get("location"))
    job_id = _safe_str(rec.get("job_id") or rec.get("id"))
    return {
        "source": source, "offer_type": offer_type,
        "external_id": job_id or url or f"gjobs-{id(item)}",
        "title": title,
        "description": _safe_str(rec.get("description"))[:2000] or None,
        "url": url or None,
        "company": company or None,
        "location": location or None,
        "raw_data": rec,
    }


def normalize_facebook_post(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    text = _safe_str(rec.get("text") or rec.get("content") or rec.get("message") or rec.get("body"))
    url = _safe_str(rec.get("url") or rec.get("postUrl") or rec.get("link"))
    if not text and not url:
        return None
    author = rec.get("author") or rec.get("user") or {}
    author_name = _safe_str(author.get("name") if isinstance(author, dict) else author)
    return {
        "source": source, "offer_type": offer_type,
        "external_id": url or _safe_str(rec.get("postId") or rec.get("id")) or f"fb-{id(item)}",
        "title": text[:120] or "Publication Facebook",
        "description": text[:2000] or None,
        "url": url or None,
        "company": author_name or None,
        "posted_at": _safe_date(rec.get("time") or rec.get("createdTime") or rec.get("date")),
        "raw_data": rec,
    }


def normalize_web_content(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    url = _safe_str(rec.get("url") or rec.get("pageUrl"))
    text = _safe_str(rec.get("text") or rec.get("content") or rec.get("markdown"))
    if not text and not url:
        return None
    meta = rec.get("metadata") or {}
    title = _safe_str(
        rec.get("title") or (meta.get("title") if isinstance(meta, dict) else None) or text[:100]
    )
    return {
        "source": source, "offer_type": offer_type,
        "external_id": url or f"web-{id(item)}",
        "title": title, "description": text[:2000] or None,
        "url": url or None, "raw_data": rec,
    }


# ── Service principal ─────────────────────────────────────────────────────────


class ApifyService:
    """Scraping multi-sources via Apify."""

    def __init__(self, db: Session, api_token: str) -> None:
        self.db = db
        self.api_token = api_token
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            from apify_client import ApifyClient
            self._client = ApifyClient(self.api_token)
        return self._client

    async def run_actor(
        self,
        *,
        actor_id: str,
        input_data: dict[str, Any],
        offer_type: str,
        source: str,
        normalizer: Normalizer,
        max_items: int | None = None,
        memory_mbytes: int = 1024,
    ) -> dict[str, int]:
        """Lance un actor Apify, normalise et stocke les résultats.

        L'appel Apify est synchrone (apify_client ne supporte pas async) — on
        l'exécute dans un thread séparé via asyncio.to_thread() pour ne pas
        bloquer la boucle FastAPI pendant les 30-300s du run.
        memory_mbytes : RAM allouée au run (défaut 1024 Mo = suffisant pour la
        plupart des actors, sauf website-content-crawler qui nécessite 2048+).
        """
        import asyncio  # noqa: PLC0415
        stats = {"scraped": 0, "stored": 0}
        try:
            client = self._get_client()

            # ── Appel synchrone isolé dans un thread ─────────────────────────
            def _run_sync() -> tuple[list, int]:
                run = client.actor(actor_id).call(
                    run_input=input_data,
                    memory_mbytes=memory_mbytes,  # option run, pas input actor
                )
                dataset_id = run.get("defaultDatasetId") if run else None
                if not dataset_id:
                    return [], 0
                result = client.dataset(dataset_id).list_items(limit=max_items or 500)
                items = result.items if hasattr(result, "items") else result.get("items", [])
                return items, len(items)

            items, n_scraped = await asyncio.to_thread(_run_sync)
            stats["scraped"] = n_scraped
            now = datetime.now(timezone.utc)

            for item in items:
                normalized = normalizer(item, offer_type, source)
                if not normalized:
                    continue
                expires = normalized.get("expires_at")
                if expires and isinstance(expires, datetime) and expires < now:
                    continue
                if self._upsert(normalized):
                    stats["stored"] += 1

            if stats["stored"]:
                try:
                    await embed_pending(self.db, limit=stats["stored"])
                except Exception:
                    logger.warning("Embedding indexation skipped after %s", actor_id, exc_info=True)
                try:
                    await extract_pending_deadlines(self.db, limit=stats["stored"])
                except Exception:
                    logger.warning("Deadline extraction skipped after %s", actor_id, exc_info=True)

        except Exception:
            logger.warning("Apify actor %s failed", actor_id, exc_info=True)

        return stats

    async def run_light(self) -> list[dict[str, Any]]:
        """Run quotidien (4x/jour) — sources principales, volume modéré."""
        results: list[dict[str, Any]] = []
        from urllib.parse import quote

        # ── Indeed — 4 pays africains supportés x 25 offres ──────────────────
        for c in INDEED_COUNTRIES:
            r = await self.run_actor(
                actor_id=ACTOR_INDEED,
                input_data={"country": c["country"], "maxItems": 25, "location": c["location"], "start": 0},
                offer_type="job", source=f"indeed_{c['country']}",
                normalizer=normalize_indeed,
            )
            results.append({"source": f"indeed_{c['country']}", "offer_type": "job", **r})

        # ── LinkedIn Jobs — hubs x mots-clés x 20 offres ──────────────────────
        # Champ confirmé : "urls" (array), pas "searchUrl"
        for location in LINKEDIN_HUBS:
            for keyword in LINKEDIN_KEYWORDS:
                search_url = f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&location={quote(location)}"
                r = await self.run_actor(
                    actor_id=ACTOR_LINKEDIN_JOBS,
                    input_data={"urls": [search_url], "maxCount": 20},
                    offer_type="job", source="linkedin_jobs",
                    normalizer=normalize_linkedin_job,
                )
                results.append({"source": "linkedin_jobs", "offer_type": "job", **r})

        # ── LinkedIn Posts — hashtags et mots-clés ─────────────────────────────
        # searchQueries : array de strings (champ confirmé dans le JSON Apify)
        for search in LINKEDIN_POST_SEARCHES:
            r = await self.run_actor(
                actor_id=ACTOR_LINKEDIN_POSTS,
                input_data={
                    "searchQueries":      [search["keywords"]],
                    "maxPosts":           25,
                    "scrapeComments":     False,
                    "scrapeReactions":    False,
                    "postNestedComments": False,
                },
                offer_type=search["offer_type"], source="linkedin_posts",
                normalizer=normalize_linkedin_post,
            )
            results.append({"source": "linkedin_posts", "offer_type": search["offer_type"], **r})

        # ── Google Jobs — query x location ────────────────────────────────────
        for q in GOOGLE_JOBS_QUERIES:
            for location in GOOGLE_JOBS_LOCATIONS:
                r = await self.run_actor(
                    actor_id=ACTOR_GOOGLE_JOBS,
                    input_data={"query": q["query"], "location": location},
                    offer_type=q["offer_type"], source="google_jobs",
                    normalizer=normalize_google_jobs,
                )
                results.append({"source": "google_jobs", "offer_type": q["offer_type"], **r})

        # ── Facebook ───────────────────────────────────────────────────────────
        # Champs confirmés : startUrls (array) + resultsLimit (pas maxPosts)
        for page in FACEBOOK_PAGES:
            r = await self.run_actor(
                actor_id=ACTOR_FACEBOOK,
                input_data={
                    "startUrls":    [{"url": page["url"]}],
                    "resultsLimit": 20,
                    "captionText":  False,
                },
                offer_type=page["offer_type"], source="facebook_africa",
                normalizer=normalize_facebook_post,
            )
            results.append({"source": "facebook_africa", "offer_type": page["offer_type"], **r})

        # ── Actors dynamiques (light) ──────────────────────────────────────────
        dynamic_results = await self._run_dynamic_actors("light")
        results.extend(dynamic_results)

        total_stored = sum(r.get("stored", 0) for r in results)
        logger.info("Apify light run done: %d sources, %d stored total", len(results), total_stored)
        return results

    async def run_heavy(self) -> list[dict[str, Any]]:
        """Run lourd hebdomadaire (2x/dim) — volume étendu + pages LinkedIn + sites web."""
        results: list[dict[str, Any]] = []
        from urllib.parse import quote

        # ── Indeed x 40 offres ─────────────────────────────────────────────────
        for c in INDEED_COUNTRIES:
            r = await self.run_actor(
                actor_id=ACTOR_INDEED,
                input_data={"country": c["country"], "maxItems": 40, "location": c["location"], "start": 0},
                offer_type="job", source=f"indeed_{c['country']}",
                normalizer=normalize_indeed,
            )
            results.append({"source": f"indeed_{c['country']}", "offer_type": "job", **r})

        # ── LinkedIn Jobs x 30 offres ──────────────────────────────────────────
        for location in LINKEDIN_HUBS:
            for keyword in LINKEDIN_KEYWORDS:
                search_url = f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&location={quote(location)}"
                r = await self.run_actor(
                    actor_id=ACTOR_LINKEDIN_JOBS,
                    input_data={"urls": [search_url], "maxCount": 30},
                    offer_type="job", source="linkedin_jobs",
                    normalizer=normalize_linkedin_job,
                )
                results.append({"source": "linkedin_jobs", "offer_type": "job", **r})

        # ── LinkedIn Pages entreprises ─────────────────────────────────────────
        # Champs confirmés par les logs : profileUrl (string) + limit (int, défaut 100)
        for page in LINKEDIN_COMPANY_PAGES:
            r = await self.run_actor(
                actor_id=ACTOR_LINKEDIN_PAGES,
                input_data={"profileUrl": page["url"], "limit": 20},
                offer_type=page["offer_type"], source="linkedin_pages",
                normalizer=normalize_linkedin_post,
            )
            results.append({"source": "linkedin_pages", "offer_type": page["offer_type"], **r})

        # ── Google Jobs — volume étendu ────────────────────────────────────────
        for q in GOOGLE_JOBS_QUERIES:
            for location in GOOGLE_JOBS_LOCATIONS:
                r = await self.run_actor(
                    actor_id=ACTOR_GOOGLE_JOBS,
                    input_data={"query": q["query"], "location": location},
                    offer_type=q["offer_type"], source="google_jobs",
                    normalizer=normalize_google_jobs,
                )
                results.append({"source": "google_jobs", "offer_type": q["offer_type"], **r})

        # ── Sites web — opportunités, job boards, bourses, subventions ─────────
        # saveMarkdown: true confirmé dans le screenshot Apify (extrait le texte propre)
        offer_type_map = {
            "opportunities": "opportunity", "job_boards": "job",
            "grants": "grant",             "scholarships": "formation",
        }
        for category, urls in WEB_URLS.items():
            r = await self.run_actor(
                actor_id=ACTOR_WEB,
                input_data={
                    "startUrls":     [{"url": u} for u in urls],
                    "maxCrawlPages": 5,
                    "maxCrawlDepth": 1,
                    "saveMarkdown":  True,
                    "saveHtml":      False,
                    "blockMedia":    True,
                },
                offer_type=offer_type_map.get(category, "opportunity"),
                source=f"web_{category}",
                normalizer=normalize_web_content,
                memory_mbytes=2048,  # website-content-crawler nécessite plus de RAM
            )
            results.append({"source": f"web_{category}", "offer_type": offer_type_map.get(category, "opportunity"), **r})

        # ── Sites web dynamiques — sources ajoutées depuis l'admin ─────────────
        dynamic = self._load_dynamic_sources()
        for category, urls in dynamic.items():
            if not urls:
                continue
            r = await self.run_actor(
                actor_id=ACTOR_WEB,
                input_data={
                    "startUrls":     [{"url": u} for u in urls],
                    "maxCrawlPages": 5,
                    "maxCrawlDepth": 1,
                    "saveMarkdown":  True,
                    "saveHtml":      False,
                    "blockMedia":    True,
                },
                offer_type=offer_type_map.get(category, "opportunity"),
                source=f"web_dynamic_{category}",
                normalizer=normalize_web_content,
                memory_mbytes=2048,
            )
            results.append({"source": f"web_dynamic_{category}", "offer_type": offer_type_map.get(category, "opportunity"), **r})

        # ── Actors dynamiques (heavy) ──────────────────────────────────────────
        dynamic_results = await self._run_dynamic_actors("heavy")
        results.extend(dynamic_results)

        total_stored = sum(r.get("stored", 0) for r in results)
        logger.info("Apify heavy run done: %d sources, %d stored total", len(results), total_stored)
        return results

    # ── Mapping normalizer_type → fonction ────────────────────────────────────

    _NORMALIZERS: dict[str, Normalizer] = {
        "indeed":       normalize_indeed,
        "linkedin_job": normalize_linkedin_job,
        "linkedin_post":normalize_linkedin_post,
        "google_jobs":  normalize_google_jobs,
        "facebook":     normalize_facebook_post,
        "web":          normalize_web_content,
    }

    async def _run_dynamic_actors(self, run_mode: str) -> list[dict]:
        """Charge les actors actifs depuis la DB et les exécute.

        run_mode : "light" ou "heavy" — filtre les actors selon leur mode.
        """
        from sqlalchemy import select as sa_select
        from app.models.scraping_actor import ScrapingActor
        try:
            rows = self.db.execute(
                sa_select(ScrapingActor).where(
                    ScrapingActor.is_active.is_(True),
                    ScrapingActor.run_mode.in_([run_mode, "both"]),
                )
            ).scalars().all()
        except Exception:
            logger.warning("_run_dynamic_actors: DB query failed", exc_info=True)
            return []

        results = []
        for actor in rows:
            normalizer = self._NORMALIZERS.get(actor.normalizer_type, normalize_web_content)
            r = await self.run_actor(
                actor_id=actor.actor_id,
                input_data=actor.input_json or {},
                offer_type=actor.offer_type,
                source=actor.source_name,
                normalizer=normalizer,
            )
            results.append({
                "source": actor.source_name,
                "offer_type": actor.offer_type,
                "actor_id": actor.actor_id,
                **r,
            })
            logger.info(
                "Dynamic actor %s (%s): scraped=%d stored=%d",
                actor.label, actor.actor_id,
                r.get("scraped", 0), r.get("stored", 0),
            )
        return results

    def _load_dynamic_sources(self) -> dict[str, list[str]]:
        """Charge les sources actives depuis la DB, groupées par catégorie."""
        from sqlalchemy import select as sa_select
        from app.models.scraping_source import ScrapingSource
        try:
            rows = self.db.execute(
                sa_select(ScrapingSource).where(ScrapingSource.is_active.is_(True))
            ).scalars().all()
        except Exception:
            logger.warning("_load_dynamic_sources: DB query failed", exc_info=True)
            return {}
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row.category, []).append(row.url)
        return grouped

    # ── Upsert commun ─────────────────────────────────────────────────────────

    def _upsert(self, data: NormalizedDict) -> bool:
        """Upsert une offre normalisée en DB. Retourne True si succès."""
        try:
            offer_type = ScrapedOfferType(data["offer_type"])
        except ValueError:
            offer_type = ScrapedOfferType.opportunity

        try:
            stmt = select(ScrapedOffer).where(
                ScrapedOffer.source == data["source"],
                ScrapedOffer.external_id == data["external_id"],
            )
            existing = self.db.execute(stmt).scalar_one_or_none()

            if existing:
                existing.title = data["title"]
                existing.description = data.get("description")
                existing.url = data.get("url")
                existing.company = data.get("company")
                existing.location = data.get("location")
                existing.salary = data.get("salary")
                existing.posted_at = data.get("posted_at")
                existing.expires_at = data.get("expires_at")
                existing.scraped_at = datetime.now(timezone.utc)
                existing.raw_data = data.get("raw_data")
                self.db.flush()
                process_offer(self.db, existing.id)
                return True

            offer = ScrapedOffer(
                source=data["source"],
                offer_type=offer_type,
                external_id=data["external_id"],
                title=data["title"],
                description=data.get("description"),
                url=data.get("url"),
                company=data.get("company"),
                location=data.get("location"),
                salary=data.get("salary"),
                posted_at=data.get("posted_at"),
                expires_at=data.get("expires_at"),
                raw_data=data.get("raw_data"),
            )
            self.db.add(offer)
            self.db.flush()
            process_offer(self.db, offer.id)
            return True
        except Exception:
            logger.debug("Apify upsert skipped (duplicate?)", exc_info=True)
            return False
