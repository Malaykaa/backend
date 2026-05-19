import logging
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

JWT_DEFAULT_SECRET = "CHANGE_ME_IN_PRODUCTION"


class Settings(BaseSettings):
    """Configuration centrale de l'application Malayka."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # Environnement
    environment: Literal["local", "dev", "staging", "prod"] = "local"
    debug: bool = True

    # Base de données (PostgreSQL)
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/malaykaa"

    # Sécurité / Auth (JWT httpOnly cookies)
    jwt_secret_key: str = JWT_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # LLM
    llm_provider: Literal["mock", "openai", "gemini", "claude"] = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-pro"
    claude_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CLAUDE_API_KEY", "ANTHROPIC_API_KEY"),
    )
    claude_model: str = "claude-sonnet-4-20250514"

    # OTP / WhatsApp — OneMessage (provider principal)
    onemessage_base_url: str | None = None
    onemessage_api_key: str | None = None

    # OTP / WhatsApp — Twilio (fallback historique, conservé pour rollback)
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_whatsapp_from: str | None = None
    otp_mock_accept_any: bool = False  # DEV ONLY — accepte tout code à 6 chiffres

    # CORS
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:5173",
        "http://192.168.1.18:5173",
        "http://192.168.1.18:5174",
        "http://192.168.1.18:5175",
    ]

    # Scraping (Apify + Perplexity)
    apify_api_token: str | None = None
    perplexity_api_key: str | None = None
    scraping_enabled: bool = False

    # Scheduler — APScheduler in-process. En multi-worker (uvicorn --workers N),
    # set SCHEDULER_ENABLED=false sur N-1 workers pour qu'un seul exécute les
    # jobs. Un advisory-lock par job sert de garde-fou si l'admin oublie.
    scheduler_enabled: bool = True
    # Fréquence par défaut (heures) du job de matching automatique. Surchargée
    # par profile.match_frequency_hours quand l'utilisateur a une préférence.
    match_default_frequency_hours: int = 6
    # Nombre d'offres à pousser à chaque run de matching (chat + notif).
    match_top_k: int = 5

    # URL publique du frontend (liens dans les notifications WhatsApp)
    frontend_url: str = "http://localhost:5173"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    @property
    def is_production(self) -> bool:
        """True pour staging/prod — environnements à exigences de sécurité strictes."""
        return self.environment in {"staging", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def validate_security_settings(settings: Settings | None = None) -> None:
    """Valide les paramètres de sécurité critiques.

    Lève RuntimeError en staging/prod si la configuration est dangereuse,
    émet des warnings sinon. Doit être appelé au démarrage de l'application.
    """
    s = settings or get_settings()
    issues: list[str] = []
    warnings: list[str] = []

    if s.jwt_secret_key == JWT_DEFAULT_SECRET or not s.jwt_secret_key:
        msg = (
            "JWT_SECRET_KEY utilise la valeur par défaut — définir une "
            "chaîne aléatoire forte (32+ caractères) via la variable d'environnement."
        )
        (issues if s.is_production else warnings).append(msg)

    onemessage_configured = bool(s.onemessage_base_url and s.onemessage_api_key)
    twilio_configured = bool(
        s.twilio_account_sid and s.twilio_auth_token and s.twilio_whatsapp_from
    )
    if s.is_production and not (onemessage_configured or twilio_configured):
        issues.append(
            "Aucun provider WhatsApp configuré — ONEMESSAGE_BASE_URL+ONEMESSAGE_API_KEY "
            "(provider principal) ou TWILIO_ACCOUNT_SID+TWILIO_AUTH_TOKEN+TWILIO_WHATSAPP_FROM "
            "(fallback) sont obligatoires en staging/prod."
        )
    if s.is_production and s.otp_mock_accept_any:
        issues.append(
            "OTP_MOCK_ACCEPT_ANY=true est interdit en staging/prod — "
            "tout code à 6 chiffres serait accepté."
        )

    _local_hosts = ("localhost", "127.0.0.1")

    if s.is_production and any(h in s.frontend_url for h in _local_hosts):
        issues.append(
            f"FRONTEND_URL='{s.frontend_url}' pointe vers localhost — "
            "définir l'URL publique du frontend (ex: https://app.malayka.com) "
            "sinon les liens dans les notifications WhatsApp seront inaccessibles."
        )

    if s.is_production and any(
        any(h in origin for h in _local_hosts) for origin in s.cors_origins
    ):
        warnings.append(
            "CORS_ORIGINS contient des origines localhost en staging/prod — "
            "vérifier que le domaine de production est configuré "
            "(ex: CORS_ORIGINS=[\"https://app.malayka.com\"])."
        )

    for warning in warnings:
        logger.warning("[security] %s", warning)

    if issues:
        bullets = "\n  - ".join(issues)
        raise RuntimeError(
            f"Refus de démarrer — configuration de sécurité invalide pour "
            f"l'environnement '{s.environment}':\n  - {bullets}"
        )
