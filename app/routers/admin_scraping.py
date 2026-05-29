"""Endpoints admin scraping — déclenchement manuel + stats + gestion des sources."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.scraping_actor import ScrapingActor
from app.models.scraping_source import ScrapingSource
from app.models.user import User
from app.repositories.scraped_offer_repo import ScrapedOfferRepository
from app.schemas.admin import (
    AdminScrapingActorCreate,
    AdminScrapingActorItem,
    AdminScrapingActorUpdate,
    AdminScrapingSourceCreate,
    AdminScrapingSourceItem,
    AdminScrapingSourceUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/scraping", tags=["admin-scraping"])


def _require_admin(user: User) -> User:
    if not user.role or user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/stats")
def scraping_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Stats des offres scrapées."""
    _require_admin(current_user)
    repo = ScrapedOfferRepository(db)
    return repo.get_stats()


async def _bg_perplexity(api_key: str) -> None:
    """Run Perplexity en arrière-plan avec sa propre session DB."""
    from app.core.database import SessionLocal
    from app.services.scraping.perplexity_service import PerplexityService
    db = SessionLocal()
    try:
        svc = PerplexityService(db, api_key)
        stats = await svc.run_daily_queries()
        db.commit()
        logger.info("Manual Perplexity run done: %s", stats)
    except Exception:
        db.rollback()
        logger.error("Manual Perplexity run failed", exc_info=True)
    finally:
        db.close()


async def _bg_apify_light(api_token: str) -> None:
    """Run Apify léger en arrière-plan avec sa propre session DB."""
    from app.core.database import SessionLocal
    from app.services.scraping.apify_service import ApifyService
    db = SessionLocal()
    try:
        svc = ApifyService(db, api_token)
        results = await svc.run_light()
        db.commit()
        total = sum(r.get("stored", 0) for r in results)
        logger.info("Manual Apify light run done: %d sources, %d stored", len(results), total)
    except Exception:
        db.rollback()
        logger.error("Manual Apify light run failed", exc_info=True)
    finally:
        db.close()


@router.post("/run-perplexity")
async def run_perplexity(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Déclenche un run Perplexity manuellement (fire-and-forget)."""
    _require_admin(current_user)
    settings = get_settings()
    if not settings.perplexity_api_key:
        raise HTTPException(status_code=400, detail="PERPLEXITY_API_KEY not configured")
    background_tasks.add_task(_bg_perplexity, settings.perplexity_api_key)
    return {"status": "started", "message": "Run Perplexity lancé en arrière-plan."}


@router.post("/run-apify-light")
async def run_apify_light(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Déclenche un run Apify léger manuellement (fire-and-forget)."""
    _require_admin(current_user)
    settings = get_settings()
    if not settings.apify_api_token:
        raise HTTPException(status_code=400, detail="APIFY_API_TOKEN not configured")
    background_tasks.add_task(_bg_apify_light, settings.apify_api_token)
    return {"status": "started", "message": "Run Apify léger lancé en arrière-plan."}


# ── Sources dynamiques ────────────────────────────────────────────────────────

@router.get("/sources", response_model=list[AdminScrapingSourceItem])
def list_sources(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Liste toutes les sources web gérées depuis l'admin."""
    _require_admin(current_user)
    rows = db.execute(
        select(ScrapingSource).order_by(ScrapingSource.created_at.desc())
    ).scalars().all()
    return [AdminScrapingSourceItem(
        id=str(r.id), url=r.url, label=r.label, category=r.category,
        notes=r.notes, is_active=r.is_active, created_at=r.created_at,
    ) for r in rows]


@router.post("/sources", response_model=AdminScrapingSourceItem, status_code=201)
def create_source(
    body: AdminScrapingSourceCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Ajoute une nouvelle source web au scraper."""
    _require_admin(current_user)
    existing = db.execute(
        select(ScrapingSource).where(ScrapingSource.url == body.url)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Cette URL existe déjà.")
    source = ScrapingSource(
        url=body.url, label=body.label,
        category=body.category, notes=body.notes,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return AdminScrapingSourceItem(
        id=str(source.id), url=source.url, label=source.label,
        category=source.category, notes=source.notes,
        is_active=source.is_active, created_at=source.created_at,
    )


@router.patch("/sources/{source_id}", response_model=AdminScrapingSourceItem)
def update_source(
    source_id: UUID,
    body: AdminScrapingSourceUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Met à jour une source (label, catégorie, notes, actif/inactif)."""
    _require_admin(current_user)
    source = db.get(ScrapingSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")
    if body.label is not None:
        source.label = body.label
    if body.category is not None:
        source.category = body.category
    if body.notes is not None:
        source.notes = body.notes
    if body.is_active is not None:
        source.is_active = body.is_active
    db.commit()
    db.refresh(source)
    return AdminScrapingSourceItem(
        id=str(source.id), url=source.url, label=source.label,
        category=source.category, notes=source.notes,
        is_active=source.is_active, created_at=source.created_at,
    )


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(
    source_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Supprime une source web."""
    _require_admin(current_user)
    source = db.get(ScrapingSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")
    db.delete(source)
    db.commit()


# ── Actors dynamiques ─────────────────────────────────────────────────────────

def _actor_item(a: ScrapingActor) -> AdminScrapingActorItem:
    return AdminScrapingActorItem(
        id=str(a.id), actor_id=a.actor_id, label=a.label,
        offer_type=a.offer_type, source_name=a.source_name,
        normalizer_type=a.normalizer_type, input_json=a.input_json or {},
        run_mode=a.run_mode, is_active=a.is_active,
        notes=a.notes, created_at=a.created_at,
    )


@router.get("/actors", response_model=list[AdminScrapingActorItem])
def list_actors(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Liste tous les actors Apify gérés depuis l'admin."""
    _require_admin(current_user)
    rows = db.execute(
        select(ScrapingActor).order_by(ScrapingActor.created_at.desc())
    ).scalars().all()
    return [_actor_item(a) for a in rows]


@router.post("/actors", response_model=AdminScrapingActorItem, status_code=201)
def create_actor(
    body: AdminScrapingActorCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Ajoute un nouvel actor Apify."""
    _require_admin(current_user)
    actor = ScrapingActor(
        actor_id=body.actor_id, label=body.label,
        offer_type=body.offer_type, source_name=body.source_name,
        normalizer_type=body.normalizer_type, input_json=body.input_json,
        run_mode=body.run_mode, notes=body.notes,
    )
    db.add(actor)
    db.commit()
    db.refresh(actor)
    return _actor_item(actor)


@router.patch("/actors/{actor_id}", response_model=AdminScrapingActorItem)
def update_actor(
    actor_id: UUID,
    body: AdminScrapingActorUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Met à jour un actor (input_json, mode, actif/inactif, etc.)."""
    _require_admin(current_user)
    actor = db.get(ScrapingActor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="Actor introuvable.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(actor, field, value)
    db.commit()
    db.refresh(actor)
    return _actor_item(actor)


@router.delete("/actors/{actor_id}", status_code=204)
def delete_actor(
    actor_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Supprime un actor."""
    _require_admin(current_user)
    actor = db.get(ScrapingActor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="Actor introuvable.")
    db.delete(actor)
    db.commit()
