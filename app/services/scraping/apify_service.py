"""Apify scraper — Indeed, LinkedIn, Reddit, Twitter, Facebook, sites web.

Architecture : la config définit quels actors lancer et comment normaliser.
runActor() est la boucle commune (appel actor → normalise → upsert → pipeline).
run_light() et run_heavy() orchestrent les runs selon la fréquence.

Porté de apify-scraper.service.ts (NestJS).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
from app.services.scraping.pipeline import embed_pending, process_offer

logger = logging.getLogger(__name__)

# ── Pays et hubs ──────────────────────────────────────────────────────────────

ALL_COUNTRIES = [
    {"country": "ci", "location": "Côte d'Ivoire"},
    {"country": "sn", "location": "Senegal"},
    {"country": "cm", "location": "Cameroon"},
    {"country": "cd", "location": "DR Congo"},
    {"country": "td", "location": "Chad"},
    {"country": "ng", "location": "Nigeria"},
    {"country": "tg", "location": "Togo"},
    {"country": "ma", "location": "Morocco"},
    {"country": "ke", "location": "Kenya"},
    {"country": "rw", "location": "Rwanda"},
    {"country": "eg", "location": "Egypt"},
    {"country": "za", "location": "South Africa"},
    {"country": "gh", "location": "Ghana"},
    {"country": "ug", "location": "Uganda"},
    {"country": "tz", "location": "Tanzania"},
    {"country": "et", "location": "Ethiopia"},
    {"country": "ml", "location": "Mali"},
    {"country": "bf", "location": "Burkina Faso"},
    {"country": "cf", "location": "Central African Republic"},
    {"country": "ga", "location": "Gabon"},
    {"country": "cg", "location": "Republic of the Congo"},
]

LINKEDIN_HUBS = [
    "Côte d'Ivoire", "Senegal", "Nigeria", "Kenya", "Morocco", "DR Congo",
    "Ghana", "Cameroon", "Egypt", "Rwanda", "Uganda", "Ethiopia", "South Africa",
]

LINKEDIN_KEYWORDS = [
    "job opportunity Africa",
    "formation gratuite Afrique",
    "bourse étude Afrique",
    "entrepreneur startup Africa",
]

REDDIT_TARGETS = [
    {"url": "https://www.reddit.com/r/africa/search/?q=scholarship+job+training+opportunity&sort=new&limit=25", "offer_type": "opportunity"},
    {"url": "https://www.reddit.com/r/westafrica/new/", "offer_type": "opportunity"},
    {"url": "https://www.reddit.com/r/africatech/new/", "offer_type": "job"},
    {"url": "https://www.reddit.com/r/learnprogramming/search/?q=Africa+scholarship+free+course&sort=new&limit=20", "offer_type": "formation"},
]

TWITTER_SEARCHES = [
    {"terms": ["#jobsafrica", "#emploiAfrique", "#africajobs"], "offer_type": "job"},
    {"terms": ["#bourseAfrique", "#scholarshipafrica", "#formationAfrique"], "offer_type": "formation"},
    {"terms": ["#subventionAfrique", "#grantafrica", "#financementPME"], "offer_type": "grant"},
    {"terms": ["#africastartup", "#entrepreneurafrique", "#appelacandidature"], "offer_type": "call_for_applications"},
]

FACEBOOK_QUERIES = [
    {"query": "bourse emploi formation Afrique", "offer_type": "formation"},
    {"query": "subvention financement startup Afrique", "offer_type": "grant"},
    {"query": "appel à candidature opportunité Afrique", "offer_type": "call_for_applications"},
]

WEB_URLS = {
    "opportunities": [
        "https://www.youthop.com/",
        "https://opportunitiesforyouth.org/",
        "https://www.afterschoolafrica.com/",
    ],
    "job_boards": [
        "https://myjobmag.com/",
        "https://www.jobberman.com/",
        "https://www.brightermonday.com/",
        "https://www.careers24.com/",
        "https://www.rekrute.com/",
    ],
    "grants": [
        "https://www.afdb.org/en/opportunities",
        "https://yali.state.gov/",
    ],
    "scholarships": [
        "https://scholarshipdb.net/scholarships-in/Africa",
        "https://scholars4dev.com/",
        "https://www.afterschoolafrica.com/scholarships/",
    ],
}


# ── Normalizers ──────────────────────────────────────────────────────────────

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


def normalize_linkedin(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    title = _safe_str(rec.get("title") or rec.get("jobTitle"))
    if not title:
        return None
    url = _safe_str(rec.get("url") or rec.get("jobUrl") or rec.get("link"))
    company_raw = rec.get("company")
    company = _safe_str(company_raw.get("name") if isinstance(company_raw, dict) else company_raw)
    return {
        "source": source, "offer_type": offer_type,
        "external_id": url or f"linkedin-{id(item)}",
        "title": title, "description": _safe_str(rec.get("description"))[:2000] or None,
        "url": url or None, "company": company or None,
        "location": _safe_str(rec.get("location")) or None,
        "salary": _safe_str(rec.get("salary")) or None,
        "posted_at": _safe_date(rec.get("postedAt") or rec.get("datePosted")),
        "raw_data": rec,
    }


def normalize_reddit(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    title = _safe_str(rec.get("title"))
    if not title:
        return None
    permalink = _safe_str(rec.get("permalink"))
    url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else _safe_str(rec.get("url")) or permalink
    return {
        "source": source, "offer_type": offer_type,
        "external_id": _safe_str(rec.get("id")) or url or f"reddit-{id(item)}",
        "title": title, "description": _safe_str(rec.get("selftext") or rec.get("body"))[:2000] or None,
        "url": url or None, "company": f"r/{rec.get('subreddit')}" if rec.get("subreddit") else None,
        "location": "Africa",
        "posted_at": _safe_date(rec.get("created_utc") or rec.get("created")),
        "raw_data": rec,
    }


def normalize_twitter(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    text = _safe_str(rec.get("text") or rec.get("fullText") or rec.get("content"))
    tweet_id = _safe_str(rec.get("id") or rec.get("tweetId"))
    url = _safe_str(rec.get("url")) or (f"https://twitter.com/i/status/{tweet_id}" if tweet_id else "")
    if not text and not url:
        return None
    author = rec.get("author") or {}
    handle = _safe_str(author.get("userName") or author.get("username") if isinstance(author, dict) else None)
    return {
        "source": source, "offer_type": offer_type,
        "external_id": tweet_id or url or f"twitter-{id(item)}",
        "title": text[:120] or "Tweet", "description": text[:2000] or None,
        "url": url or None, "company": f"@{handle}" if handle else None,
        "location": "Africa",
        "posted_at": _safe_date(rec.get("createdAt") or rec.get("created_at")),
        "raw_data": rec,
    }


def normalize_facebook_post(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    text = _safe_str(rec.get("text") or rec.get("content") or rec.get("message"))
    url = _safe_str(rec.get("url") or rec.get("postUrl"))
    if not text and not url:
        return None
    author = rec.get("author") or {}
    return {
        "source": source, "offer_type": offer_type,
        "external_id": url or f"fb-post-{id(item)}",
        "title": text[:120] or "Publication Facebook",
        "description": text[:2000] or None, "url": url or None,
        "company": _safe_str(author.get("name") if isinstance(author, dict) else author) or None,
        "posted_at": _safe_date(rec.get("createdTime") or rec.get("date")),
        "raw_data": rec,
    }


def normalize_web_content(item: Any, offer_type: str, source: str) -> NormalizedDict | None:
    rec = item if isinstance(item, dict) else {}
    url = _safe_str(rec.get("url") or rec.get("pageUrl"))
    text = _safe_str(rec.get("text") or rec.get("content") or rec.get("markdown"))
    if not text and not url:
        return None
    meta = rec.get("metadata") or {}
    title = _safe_str(rec.get("title") or (meta.get("title") if isinstance(meta, dict) else None) or text[:100])
    return {
        "source": source, "offer_type": offer_type,
        "external_id": url or f"web-{id(item)}",
        "title": title, "description": text[:2000] or None,
        "url": url or None, "raw_data": rec,
    }


# ── Service principal ────────────────────────────────────────────────────────


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
    ) -> dict[str, int]:
        """Lance un actor Apify, normalise et stocke les résultats."""
        stats = {"scraped": 0, "stored": 0}
        try:
            client = self._get_client()
            run = client.actor(actor_id).call(input_data, wait_secs=300)
            dataset_id = run.get("defaultDatasetId")
            if not dataset_id:
                return stats

            result = client.dataset(dataset_id).list_items()
            items = result.get("items", [])
            stats["scraped"] = len(items)
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

            # Vectoriser le batch fraîchement ingéré (no-op si Perplexity absent).
            if stats["stored"]:
                try:
                    await embed_pending(self.db, limit=stats["stored"])
                except Exception:
                    logger.warning("Embedding indexation skipped after %s", actor_id, exc_info=True)

        except Exception:
            logger.warning("Apify actor %s failed", actor_id, exc_info=True)

        return stats

    async def run_light(self) -> list[dict[str, Any]]:
        """Run quotidien (4x/jour) — tous pays x actors principaux."""
        results: list[dict[str, Any]] = []

        # Indeed — tous les pays x 25 items
        for c in ALL_COUNTRIES:
            r = await self.run_actor(
                actor_id="valig/indeed-jobs-scraper",
                input_data={"country": c["country"], "maxItems": 25, "location": c["location"], "start": 0},
                offer_type="job", source=f"indeed_{c['country']}",
                normalizer=normalize_indeed,
            )
            results.append({"source": f"indeed_{c['country']}", "offer_type": "job", **r})

        # LinkedIn — tous hubs x keywords x 20 items
        for location in LINKEDIN_HUBS:
            for keyword in LINKEDIN_KEYWORDS:
                from urllib.parse import quote
                r = await self.run_actor(
                    actor_id="labrat011/linkedin-jobs-scraper",
                    input_data={"searchUrl": f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&location={quote(location)}", "maxItems": 20},
                    offer_type="job", source="linkedin",
                    normalizer=normalize_linkedin,
                )
                results.append({"source": "linkedin", "offer_type": "job", **r})

        # Reddit
        for target in REDDIT_TARGETS:
            r = await self.run_actor(
                actor_id="apify/reddit-scraper",
                input_data={"startUrls": [{"url": target["url"]}], "maxItems": 20},
                offer_type=target["offer_type"], source="reddit",
                normalizer=normalize_reddit,
            )
            results.append({"source": "reddit", "offer_type": target["offer_type"], **r})

        # Twitter
        for search in TWITTER_SEARCHES:
            r = await self.run_actor(
                actor_id="quacker/twitter-scraper",
                input_data={"searchTerms": search["terms"], "maxTweets": 30, "language": "fr,en"},
                offer_type=search["offer_type"], source="twitter",
                normalizer=normalize_twitter,
            )
            results.append({"source": "twitter", "offer_type": search["offer_type"], **r})

        # Facebook posts
        for fb in FACEBOOK_QUERIES:
            r = await self.run_actor(
                actor_id="scraper_one/facebook-posts-search",
                input_data={"query": f"{fb['query']} Afrique", "resultsCount": 20, "location": "Afrique"},
                offer_type=fb["offer_type"], source="facebook_africa",
                normalizer=normalize_facebook_post,
            )
            results.append({"source": "facebook_africa", "offer_type": fb["offer_type"], **r})

        total_stored = sum(r.get("stored", 0) for r in results)
        logger.info("Apify light run done: %d sources, %d stored total", len(results), total_stored)
        return results

    async def run_heavy(self) -> list[dict[str, Any]]:
        """Run lourd hebdomadaire (2x/dim) — volume étendu + sites web."""
        results: list[dict[str, Any]] = []

        # Indeed x 40 items
        for c in ALL_COUNTRIES:
            r = await self.run_actor(
                actor_id="valig/indeed-jobs-scraper",
                input_data={"country": c["country"], "maxItems": 40, "location": c["location"], "start": 0},
                offer_type="job", source=f"indeed_{c['country']}",
                normalizer=normalize_indeed,
            )
            results.append({"source": f"indeed_{c['country']}", "offer_type": "job", **r})

        # LinkedIn x 30 items
        for location in LINKEDIN_HUBS:
            for keyword in LINKEDIN_KEYWORDS:
                from urllib.parse import quote
                r = await self.run_actor(
                    actor_id="labrat011/linkedin-jobs-scraper",
                    input_data={"searchUrl": f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&location={quote(location)}", "maxItems": 30},
                    offer_type="job", source="linkedin",
                    normalizer=normalize_linkedin,
                )
                results.append({"source": "linkedin", "offer_type": "job", **r})

        # Web sites — opportunités, job boards, bourses, subventions
        for category, urls in WEB_URLS.items():
            offer_type_map = {
                "opportunities": "opportunity", "job_boards": "job",
                "grants": "grant", "scholarships": "formation",
            }
            r = await self.run_actor(
                actor_id="apify/website-content-crawler",
                input_data={"startUrls": [{"url": u} for u in urls], "maxCrawlPages": 8, "maxCrawlDepth": 2},
                offer_type=offer_type_map.get(category, "opportunity"),
                source=f"web_{category}",
                normalizer=normalize_web_content,
            )
            results.append({"source": f"web_{category}", "offer_type": offer_type_map.get(category, "opportunity"), **r})

        total_stored = sum(r.get("stored", 0) for r in results)
        logger.info("Apify heavy run done: %d sources, %d stored total", len(results), total_stored)
        return results

    # ── Upsert commun ────────────────────────────────────────────────────────

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
