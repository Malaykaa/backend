"""Service OTP — stockage Redis avec fallback mémoire en dev.

En staging/prod, Redis est obligatoire : si la connexion échoue, le service
refuse de démarrer (fail-fast). En local/dev, fallback mémoire si Redis
indisponible (avec warning).
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class _OtpEntry:
    __slots__ = ("code", "expires_at")

    def __init__(self, code: str, ttl: timedelta) -> None:
        self.code = code
        self.expires_at = datetime.now(timezone.utc) + ttl


class OtpService:
    """Stockage OTP — Redis si disponible, mémoire sinon (dev uniquement)."""

    TTL_SECONDS = 600
    KEY_PREFIX = "otp:"

    def __init__(self) -> None:
        self._memory: dict[str, _OtpEntry] = {}
        self._redis: redis.Redis | None = None
        self._connect_redis()

    def _connect_redis(self) -> None:
        settings = get_settings()
        if not settings.redis_url:
            self._fail_or_warn(settings, "REDIS_URL non défini")
            return
        try:
            client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
            self._redis = client
            logger.info("[OtpService] Redis connecté (%s)", _redact(settings.redis_url))
        except (redis.RedisError, OSError) as exc:
            self._fail_or_warn(settings, f"Redis inaccessible : {exc}")

    @staticmethod
    def _fail_or_warn(settings, reason: str) -> None:
        if settings.is_production:
            raise RuntimeError(
                f"Refus de démarrer — Redis est obligatoire en "
                f"'{settings.environment}'. {reason}"
            )
        logger.warning(
            "[OtpService] %s — fallback mémoire (autorisé uniquement en dev)", reason
        )

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Supprime tous les caractères non-numériques."""
        return "".join(c for c in phone if c.isdigit())

    def generate_code(self) -> str:
        """Génère un code OTP à 6 chiffres cryptographiquement aléatoire."""
        return f"{secrets.randbelow(900000) + 100000}"

    def _key(self, phone: str) -> str:
        return f"{self.KEY_PREFIX}{self.normalize_phone(phone)}"

    def store(self, phone: str, code: str) -> None:
        key = self._key(phone)
        if self._redis is not None:
            self._redis.set(key, code, ex=self.TTL_SECONDS)
            return
        self._memory[key] = _OtpEntry(code, timedelta(seconds=self.TTL_SECONDS))

    def verify(self, phone: str, code: str) -> bool:
        """Vérifie le code et le supprime atomiquement (usage unique)."""
        key = self._key(phone)
        if self._redis is not None:
            stored = self._redis.getdel(key)
            return stored is not None and stored == code

        entry = self._memory.get(key)
        if entry is None:
            return False
        if datetime.now(timezone.utc) > entry.expires_at:
            self._memory.pop(key, None)
            return False
        if entry.code != code:
            return False
        self._memory.pop(key, None)
        return True

    def invalidate(self, phone: str) -> None:
        key = self._key(phone)
        if self._redis is not None:
            self._redis.delete(key)
            return
        self._memory.pop(key, None)


def _redact(url: str) -> str:
    """Masque le mot de passe d'une URL Redis pour le log."""
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if "://" in head:
        scheme, _ = head.split("://", 1)
        return f"{scheme}://***@{tail}"
    return f"***@{tail}"


otp_service = OtpService()
