"""Abonnements aux notifications push web (PWA).

Trois routes seulement : la clé publique dont le navigateur a besoin pour
s'abonner, et l'abonnement/désabonnement lui-même. L'envoi effectif vit dans
`app.services.push_service`, appelé depuis les points de création de
notifications — jamais depuis ce routeur.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.schemas.push import PushSubscribeRequest, PushUnsubscribeRequest

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key")
def get_vapid_public_key():
    """Clé publique VAPID — vide si le push n'est pas configuré côté serveur.

    Le frontend s'en sert pour vérifier, avant même de proposer l'activation
    des notifications, que le serveur est en mesure d'en envoyer.
    """
    return {"public_key": get_settings().vapid_public_key}


@router.post("/subscribe", status_code=204)
def subscribe(
    payload: PushSubscribeRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Enregistre — ou met à jour — l'abonnement de cet appareil.

    `endpoint` est unique en base : re-souscrire depuis le même appareil
    (redémarrage, permission redemandée) met à jour la ligne existante,
    y compris si elle appartenait avant à un autre compte sur le même
    navigateur partagé.
    """
    existing = db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    ).scalar_one_or_none()

    if existing:
        existing.user_id = current_user.id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        existing.user_agent = request.headers.get("user-agent", "")[:300] or None
    else:
        db.add(PushSubscription(
            user_id=current_user.id,
            endpoint=payload.endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            user_agent=request.headers.get("user-agent", "")[:300] or None,
        ))

    db.commit()


@router.post("/unsubscribe", status_code=204)
def unsubscribe(
    payload: PushUnsubscribeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    sub = db.execute(
        select(PushSubscription).where(
            PushSubscription.endpoint == payload.endpoint,
            PushSubscription.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if sub:
        db.delete(sub)
        db.commit()
