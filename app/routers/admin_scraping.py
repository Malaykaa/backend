"""Endpoints admin scraping — déclenchement manuel + stats."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.repositories.scraped_offer_repo import ScrapedOfferRepository

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


@router.post("/run-perplexity")
async def run_perplexity(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Déclenche un run Perplexity manuellement."""
    _require_admin(current_user)
    settings = get_settings()
    if not settings.perplexity_api_key:
        raise HTTPException(status_code=400, detail="PERPLEXITY_API_KEY not configured")

    from app.services.scraping.perplexity_service import PerplexityService
    svc = PerplexityService(db, settings.perplexity_api_key)
    stats = await svc.run_daily_queries()
    db.commit()
    return stats


@router.post("/run-apify-light")
async def run_apify_light(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Déclenche un run Apify léger manuellement."""
    _require_admin(current_user)
    settings = get_settings()
    if not settings.apify_api_token:
        raise HTTPException(status_code=400, detail="APIFY_API_TOKEN not configured")

    from app.services.scraping.apify_service import ApifyService
    svc = ApifyService(db, settings.apify_api_token)
    results = await svc.run_light()
    db.commit()
    total = sum(r.get("stored", 0) for r in results)
    return {"sources": len(results), "total_stored": total, "details": results}
