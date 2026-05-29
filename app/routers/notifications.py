"""Router Notifications — alertes in-app de matching haute-pertinence."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.notification import UserNotification
from app.models.user import User

router = APIRouter(prefix="/notifications", tags=["notifications"])

_MAX_RETURNED = 30  # max notifications retournées par appel


@router.get("/")
def get_notifications(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Retourne les notifications non-lues + les 10 dernières lues."""
    rows = db.execute(
        select(UserNotification)
        .where(UserNotification.user_id == current_user.id)
        .order_by(UserNotification.created_at.desc())
        .limit(_MAX_RETURNED)
    ).scalars().all()

    return {
        "notifications": [_serialize(n) for n in rows],
        "unread_count": sum(1 for n in rows if not n.seen),
    }


@router.post("/mark-read")
def mark_all_read(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Marque toutes les notifications non-lues comme vues."""
    db.execute(
        update(UserNotification)
        .where(
            UserNotification.user_id == current_user.id,
            UserNotification.seen.is_(False),
        )
        .values(seen=True)
    )
    db.commit()
    return {"ok": True}


@router.post("/{notification_id}/read")
def mark_one_read(
    notification_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Marque une notification spécifique comme vue."""
    db.execute(
        update(UserNotification)
        .where(
            UserNotification.id == notification_id,
            UserNotification.user_id == current_user.id,
        )
        .values(seen=True)
    )
    db.commit()
    return {"ok": True}


def _serialize(n: UserNotification) -> dict:
    return {
        "id":          str(n.id),
        "offer_id":    str(n.offer_id) if n.offer_id else None,
        "offer_title": n.offer_title,
        "offer_url":   n.offer_url,
        "offer_type":  n.offer_type,
        "score_pct":   n.score_pct,
        "seen":        n.seen,
        "created_at":  n.created_at.isoformat() if n.created_at else None,
    }
