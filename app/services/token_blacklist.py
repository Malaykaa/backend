"""Révocation des refresh tokens à la déconnexion — blacklist Redis.

Stratégie :
- À la déconnexion / rotation : le refresh token est hashé (SHA-256) et stocké
  dans Redis avec un TTL égal à sa durée de validité restante.
- Au refresh : on vérifie la présence du hash avant d'accepter le token.
- Clé Redis : `rl:{sha256(token)}` → valeur "1", TTL = secondes restantes.

Comportement en cas d'indisponibilité Redis :
- Si BLACKLIST_FAIL_CLOSED=false (défaut / dev) : fail-open — log warning,
  les tokens révoqués restent utilisables jusqu'à expiration naturelle.
- Si BLACKLIST_FAIL_CLOSED=true (recommandé en prod) : fail-closed —
  is_revoked() lève UnauthorizedError, bloquant tout refresh pendant l'indispo.

Reconnexion automatique : après une erreur Redis, le client est remis à None et
un cooldown de 30 s est activé avant toute nouvelle tentative de connexion.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

import redis as redis_lib

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_RETRY_COOLDOWN_SECONDS = 30

_redis_client: redis_lib.Redis | None = None
_redis_unavailable_until: float = 0.0  # monotonic timestamp; 0 = pas en cooldown


def _client() -> redis_lib.Redis | None:
    """Retourne le client Redis partagé.

    Tente une reconnexion après le cooldown si le client est absent.
    Retourne None si Redis est indisponible ou non configuré.
    """
    global _redis_client, _redis_unavailable_until

    if _redis_client is not None:
        return _redis_client

    now = time.monotonic()
    if _redis_unavailable_until > now:
        return None  # cooldown actif — ne pas retenter

    settings = get_settings()
    if not settings.redis_url:
        _redis_unavailable_until = now + _RETRY_COOLDOWN_SECONDS
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
        _redis_unavailable_until = 0.0
        return _redis_client
    except Exception as exc:
        _redis_unavailable_until = now + _RETRY_COOLDOWN_SECONDS
        logger.warning(
            "[TokenBlacklist] Redis inaccessible — révocation désactivée pendant %ds : %s",
            _RETRY_COOLDOWN_SECONDS,
            exc,
        )
        return None


def _reset_client() -> None:
    """Invalide le client Redis courant pour forcer une reconnexion au prochain appel."""
    global _redis_client, _redis_unavailable_until
    _redis_client = None
    _redis_unavailable_until = time.monotonic() + _RETRY_COOLDOWN_SECONDS


def _key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"rl:{digest}"


def revoke(token: str, exp: int) -> None:
    """Ajoute le token à la blacklist jusqu'à son expiration naturelle.

    exp : timestamp UNIX (champ 'exp' du payload JWT).
    Non bloquant : si Redis est indisponible, un warning est loggé mais
    l'opération ne fait pas échouer l'appel (logout/rotation doit toujours
    réussir côté client — les cookies sont supprimés dans tous les cas).
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
        _reset_client()
        logger.error("[TokenBlacklist] Échec de révocation : %s", exc)


def is_revoked(token: str) -> bool:
    """Retourne True si le token a été révoqué.

    Comportement si Redis est indisponible :
    - fail-open  (BLACKLIST_FAIL_CLOSED=false) : retourne False, log warning.
    - fail-closed (BLACKLIST_FAIL_CLOSED=true)  : lève UnauthorizedError.
    """
    from app.core.exceptions import UnauthorizedError

    client = _client()
    if client is None:
        settings = get_settings()
        if settings.blacklist_fail_closed:
            logger.error(
                "[TokenBlacklist] Redis indisponible en mode fail-closed — "
                "refresh refusé pour sécurité."
            )
            raise UnauthorizedError(
                "Service d'authentification temporairement indisponible. "
                "Veuillez réessayer dans quelques instants."
            )
        logger.warning(
            "[TokenBlacklist] Vérification de révocation ignorée — Redis indisponible (fail-open)"
        )
        return False

    try:
        return bool(client.exists(_key(token)))
    except Exception as exc:
        _reset_client()
        logger.error("[TokenBlacklist] Échec de vérification : %s", exc)
        settings = get_settings()
        if settings.blacklist_fail_closed:
            raise UnauthorizedError(
                "Service d'authentification temporairement indisponible. "
                "Veuillez réessayer dans quelques instants."
            )
        return False
