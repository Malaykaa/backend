"""Scheduler — APScheduler pour scraping et matching automatique.

Démarre QUE si SCHEDULER_ENABLED=true (default true). En multi-worker
(uvicorn --workers N), il faut explicitement set SCHEDULER_ENABLED=false
sur N-1 workers pour qu'un seul exécute les jobs.

Garde-fou supplémentaire : chaque job tente un pg_try_advisory_xact_lock
au démarrage. Si un autre worker tient déjà le lock pour ce même job,
ce worker skip son exécution. Ça évite les doublons même si plusieurs
workers ont SCHEDULER_ENABLED=true par erreur.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Clés numériques uniques pour les advisory-locks par job. Choisies
# arbitrairement (entiers 64-bit). Ne pas réutiliser ailleurs dans l'app.
_LOCK_KEY_APIFY_LIGHT               = 7_001_001
_LOCK_KEY_APIFY_HEAVY               = 7_001_002
_LOCK_KEY_PERPLEXITY                = 7_001_003
_LOCK_KEY_MATCH_RUNNER              = 7_001_004
_LOCK_KEY_PERPLEXITY_SEARCH_AFRICA  = 7_001_005   # /search Afrique — 06h UTC
_LOCK_KEY_PERPLEXITY_SEARCH_GLOBAL  = 7_001_006   # /search Mondial — 18h UTC


def _try_lock(db, key: int) -> bool:
    """pg_try_advisory_xact_lock — relâché auto au commit/rollback de la txn.

    À utiliser pour les jobs qui ne committent qu'à la fin (apify, perplexity).
    """
    return bool(
        db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key}).scalar()
    )


def _try_session_lock(db, key: int) -> bool:
    """pg_try_advisory_lock — relâché à la déconnexion de la session.

    À utiliser pour les jobs qui committent en cours de route (match runner).
    L'appelant DOIT garder la session ouverte tant que le job tourne.
    """
    return bool(
        db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
    )


async def _run_apify_light() -> None:
    """Run Apify léger — 4x/jour."""
    settings = get_settings()
    if not settings.apify_api_token:
        return
    db = SessionLocal()
    try:
        if not _try_lock(db, _LOCK_KEY_APIFY_LIGHT):
            logger.info("Apify light skipped — held by another worker")
            return
        from app.services.scraping.apify_service import ApifyService
        svc = ApifyService(db, settings.apify_api_token)
        results = await svc.run_light()
        db.commit()
        total = sum(r.get("stored", 0) for r in results)
        logger.info("Scheduled Apify light: %d stored", total)
    except Exception:
        db.rollback()
        logger.error("Scheduled Apify light failed", exc_info=True)
    finally:
        db.close()


async def _run_apify_heavy() -> None:
    """Run Apify lourd — 2x/dimanche."""
    settings = get_settings()
    if not settings.apify_api_token:
        return
    db = SessionLocal()
    try:
        if not _try_lock(db, _LOCK_KEY_APIFY_HEAVY):
            logger.info("Apify heavy skipped — held by another worker")
            return
        from app.services.scraping.apify_service import ApifyService
        svc = ApifyService(db, settings.apify_api_token)
        results = await svc.run_heavy()
        db.commit()
        total = sum(r.get("stored", 0) for r in results)
        logger.info("Scheduled Apify heavy: %d stored", total)
    except Exception:
        db.rollback()
        logger.error("Scheduled Apify heavy failed", exc_info=True)
    finally:
        db.close()


async def _run_perplexity_daily() -> None:
    """Run Perplexity chat/completions — 2x/jour (analyse qualitative)."""
    settings = get_settings()
    if not settings.perplexity_api_key:
        return
    db = SessionLocal()
    try:
        if not _try_lock(db, _LOCK_KEY_PERPLEXITY):
            logger.info("Perplexity daily skipped — held by another worker")
            return
        from app.services.scraping.perplexity_service import PerplexityService
        svc = PerplexityService(db, settings.perplexity_api_key)
        stats = await svc.run_daily_queries()
        db.commit()
        logger.info("Scheduled Perplexity chat: %s", stats)
    except Exception:
        db.rollback()
        logger.error("Scheduled Perplexity chat failed", exc_info=True)
    finally:
        db.close()


async def _run_perplexity_search_africa() -> None:
    """Run Perplexity /search — catalogue Afrique, 06h UTC.

    ~38 requêtes ciblant Afrique, Afrique francophone, sub-Saharan Africa.
    """
    settings = get_settings()
    if not settings.perplexity_api_key:
        return
    db = SessionLocal()
    try:
        if not _try_lock(db, _LOCK_KEY_PERPLEXITY_SEARCH_AFRICA):
            logger.info("Perplexity search/africa skipped — held by another worker")
            return
        from app.services.scraping.perplexity_search_scraper import PerplexitySearchScraper
        svc = PerplexitySearchScraper(db, settings.perplexity_api_key)
        stats = await svc.run_africa()
        db.commit()
        logger.info("Scheduled Perplexity search/africa: %s", stats)
    except Exception:
        db.rollback()
        logger.error("Scheduled Perplexity search/africa failed", exc_info=True)
    finally:
        db.close()


async def _run_perplexity_search_global() -> None:
    """Run Perplexity /search — catalogue mondial, 18h UTC.

    ~40 requêtes internationales sans restriction géographique,
    accessibles depuis l'Afrique (remote, institutions globales, programmes mondiaux).
    """
    settings = get_settings()
    if not settings.perplexity_api_key:
        return
    db = SessionLocal()
    try:
        if not _try_lock(db, _LOCK_KEY_PERPLEXITY_SEARCH_GLOBAL):
            logger.info("Perplexity search/global skipped — held by another worker")
            return
        from app.services.scraping.perplexity_search_scraper import PerplexitySearchScraper
        svc = PerplexitySearchScraper(db, settings.perplexity_api_key)
        stats = await svc.run_global()
        db.commit()
        logger.info("Scheduled Perplexity search/global: %s", stats)
    except Exception:
        db.rollback()
        logger.error("Scheduled Perplexity search/global failed", exc_info=True)
    finally:
        db.close()


async def _run_match_job() -> None:
    """Matching automatique — toutes les heures.

    MatchRunner filtre lui-même par fréquence personnalisée
    (profile.match_frequency_hours), donc on peut tourner à granularité
    horaire sans surconsommer : un user 24h ne sera traité qu'une fois
    par jour, un user 6h tous les 6 ticks.

    Lock session-scoped séparé du DB de travail : MatchRunner committe par
    user, ce qui libérerait un lock xact-scoped en cours de job. La session
    `lock_db` reste ouverte pour la durée du job ; sa fermeture relâche
    le lock côté Postgres.
    """
    lock_db = SessionLocal()
    try:
        if not _try_session_lock(lock_db, _LOCK_KEY_MATCH_RUNNER):
            logger.info("Match job skipped — held by another worker")
            return
        work_db = SessionLocal()
        try:
            from app.llm import get_llm_provider
            from app.services.match_runner import MatchRunner
            runner = MatchRunner(work_db, llm=get_llm_provider())
            stats = await runner.run_once()
            logger.info("Scheduled match job: %s", stats)
        except Exception:
            work_db.rollback()
            logger.error("Scheduled match job failed", exc_info=True)
        finally:
            work_db.close()
    finally:
        lock_db.close()  # libère le pg_advisory_lock


async def _run_daily_scraping() -> None:
    """Run combiné Apify léger + tous les Perplexity — exécuté 3x/jour lun-sam."""
    await _run_apify_light()
    await _run_perplexity_daily()
    await _run_perplexity_search_africa()
    await _run_perplexity_search_global()


def start_scheduler() -> None:
    """Démarre le scheduler si les conditions d'activation sont réunies.

    Logique d'activation (court-circuit dans l'ordre) :
    1. SCHEDULER_ENABLED=false → ne démarre jamais dans ce process.
    2. SCHEDULER_WORKER_ONLY=true → ne démarre que si SCHEDULER_WORKER=true
       est aussi défini (worker désigné explicitement).
    3. Sinon → démarre normalement (single-process ou Option A multi-worker).
    """
    global _scheduler

    settings = get_settings()

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
        return

    if settings.scheduler_worker_only and not settings.scheduler_worker:
        logger.info(
            "Scheduler skipped — SCHEDULER_WORKER_ONLY=true mais SCHEDULER_WORKER=false "
            "pour ce process (PID %d)", __import__("os").getpid(),
        )
        return

    if _scheduler is not None:
        logger.warning("Scheduler already running in this process")
        return

    _scheduler = AsyncIOScheduler()

    # Scraping combiné (Apify léger + Perplexity) : 3x/jour lun-sam
    # 07h30, 13h00, 17h30 GMT — chaque sous-job a son propre advisory lock.
    if settings.scraping_enabled:
        for slot_hour, slot_minute, slot_id in [
            (7, 30, "morning"),
            (13, 0, "noon"),
            (17, 30, "evening"),
        ]:
            _scheduler.add_job(
                _run_daily_scraping, "cron",
                day_of_week="mon-sat",
                hour=slot_hour, minute=slot_minute,
                id=f"daily_scraping_{slot_id}",
            )
        logger.info("Scheduled daily scraping 3x/day Mon–Sat (07:30, 13:00, 17:30 GMT)")

    # Apify heavy : dimanche uniquement (07h00, 15h00 GMT)
    if settings.scraping_enabled and settings.apify_api_token:
        _scheduler.add_job(
            _run_apify_heavy, "cron",
            day_of_week="sun", hour="7,15",
            id="apify_heavy",
        )
        logger.info("Scheduled Apify heavy (Sunday 07:00 & 15:00 GMT)")

    # Matching automatique : toutes les heures, à xx:30 pour décaler
    # des fenêtres de scraping (laisse l'indexation embeddings se terminer).
    _scheduler.add_job(_run_match_job, "cron", minute=30, id="match_job")
    logger.info("Scheduled auto-match job (hourly @ :30)")

    _scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler() -> None:
    """Arrête le scheduler proprement."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
