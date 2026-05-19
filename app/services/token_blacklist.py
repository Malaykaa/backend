"""Révocation des refresh tokens à la déconnexion — blacklist Redis.

Stratégie :
- À la déconnexion : le refresh token est hashé (SHA-256) et stocké dans Redis
  avec un TTL égal à sa durée de validité restante.
- Au refresh : on vérifie la présence du hash avant d'accepter le token.
- Stockage : clé Redis `rl:{sha256(token)}` → valeur "1", TTL = secondes restantes.

Comportement en cas d'indisponibilité Redis :
- Fail-open : on log un warning et on laisse passer (ne bloque pas les utilisateurs).
- Acceptable pour un MVP : le pire cas est qu'un token révoqué reste utilisable
  jusqu'à expiration naturelle (7 jours max) si Redis est hors-ligne.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import redis as redis_lib

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: redis_lib.Redis | None = None
_redis_unavailable: bool = False  # évite de retenter si déjà connu comme hors-ligne


def _client() -> redis_lib.Redis | None:
    """Retourne le client Redis partagé, None si indisponible."""
    global _redis_client, _redis_unavailable
    if _redis_unavailable:
        return None
    if _redis_client is not None:
        return _redis_client
    settings = get_settings()
    if not settings.redis_url:
        _redis_unavailable = True
        return None
    try:
        client = redis_lib.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception as exc:
        _redis_unavailable = True
        logger.warning("[TokenBlacklist] Redis inaccessible — révocation désactivée : %s", exc)
        return None


def _key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"rl:{digest}"


def revoke(token: str, exp: int) -> None:
    """Ajoute le refresh token à la blacklist jusqu'à son expiration naturelle.

    exp : timestamp UNIX (champ 'exp' du payload JWT).
    """
    client = _client()
    if client is None:
        logger.warning("[TokenBlacklist] Token non révoqué (Redis indisponible)")
        return
    ttl = exp - int(datetime.now(timezone.utc).timestamp())
    if ttl <= 0:
        return  # token déjà expiré — inutile de stocker
    try:
        client.setex(_key(token), ttl, "1")
    except Exception as exc:
        logger.error("[TokenBlacklist] Échec de révocation : %s", exc)


def is_revoked(token: str) -> bool:
    """Retourne True si le token a été révoqué, False sinon (ou si Redis down)."""
    client = _client()
    if client is None:
        return False  # fail-open : ne bloque pas les utilisateurs si Redis down
    try:
        return bool(client.exists(_key(token)))
    except Exception as exc:
        logger.error("[TokenBlacklist] Échec de vérification : %s", exc)
        return False  # fail-open sur erreur réseau
