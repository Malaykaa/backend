"""Notifications push web (PWA) — Web Push standard, chiffré, via VAPID.

Toujours en complément d'une notification in-app déjà créée, jamais à sa
place : si l'envoi échoue (réseau, abonnement périmé, clés absentes), la
notification reste visible dans le panneau de l'application. C'est pour ça
que toute erreur ici est avalée après journalisation — jamais propagée.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)

_warned_missing_keys = False


def send_push(
    db: Session, *, user_id: uuid.UUID, title: str, body: str | None = None, url: str | None = None,
) -> None:
    """Pousse une notification vers tous les appareils abonnés de l'utilisateur.

    Sans clés VAPID configurées, ne fait rien — la plateforme n'est pas
    encore prête à envoyer du push, ce qui n'empêche pas le reste de
    fonctionner. Un abonnement dont le navigateur signale qu'il n'existe
    plus (404/410 — désinstallation, changement d'appareil) est supprimé
    immédiatement : le réessayer indéfiniment n'aurait aucun sens.
    """
    settings = get_settings()
    if not settings.vapid_public_key or not settings.vapid_private_key:
        global _warned_missing_keys
        if not _warned_missing_keys:
            logger.warning(
                "[Push] Clés VAPID absentes (VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY) — "
                "notifications push désactivées, les notifications in-app continuent."
            )
            _warned_missing_keys = True
        return

    subs = db.execute(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    ).scalars().all()
    if not subs:
        return

    # Imports différés : pywebpush embarque cryptography/aiohttp, inutiles
    # tant qu'aucun envoi n'est réellement tenté.
    from py_vapid import Vapid02
    from pywebpush import WebPushException, webpush

    vapid_key = Vapid02.from_pem(settings.vapid_private_key.encode())
    payload = json.dumps({
        "title": title[:200],
        "body": (body or "")[:300],
        "url": url or "/app",
    })

    stale: list[PushSubscription] = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=vapid_key,
                vapid_claims={"sub": f"mailto:{settings.vapid_claim_email}"},
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                stale.append(sub)
            else:
                logger.warning(
                    "[Push] échec d'envoi (%s) endpoint=%s…", status, sub.endpoint[:60],
                    exc_info=True,
                )
        except Exception:
            logger.warning("[Push] erreur inattendue lors de l'envoi", exc_info=True)

    for sub in stale:
        db.delete(sub)
    if stale:
        db.flush()
