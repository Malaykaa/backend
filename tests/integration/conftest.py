"""Fixtures pour les tests d'intégration.

Démarre un Postgres éphémère (image pgvector/pgvector:pg16) pour la session,
crée toutes les tables via SQLAlchemy, et override get_db pour pointer dessus.

Les tests sont skippés si Docker n'est pas disponible (env CI sans Docker, etc.).

Variables d'environnement forcées AVANT l'import de l'app :
- ENVIRONMENT=local         → désactive le fail-fast prod (Redis, secrets)
- LLM_PROVIDER=mock         → pas d'appel réseau LLM
- OTP_MOCK_ACCEPT_ANY=true  → accepte tout code à 6 chiffres en /auth/verify-otp
- SCHEDULER_ENABLED=false   → pas d'APScheduler en background
- REDIS_URL invalide        → fallback mémoire pour OtpService
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio

# ── Env d'abord, app ensuite ────────────────────────────────────────────────

# NB: on FORCE ces vars (pas setdefault) pour neutraliser un .env local
# qui pointerait vers de vrais providers (OneMessage, Twilio, Redis prod…).
os.environ["ENVIRONMENT"] = "local"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["OTP_MOCK_ACCEPT_ANY"] = "true"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"  # port invalide → fallback mémoire
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-prod-32chars!!"
# Désactive les providers WhatsApp pour que send_otp log seulement (pas de réseau)
os.environ["ONEMESSAGE_BASE_URL"] = ""
os.environ["ONEMESSAGE_API_KEY"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_WHATSAPP_FROM"] = ""


# ── Postgres container (session-scoped) ─────────────────────────────────────


@pytest.fixture(scope="session")
def postgres_url() -> Generator[str, None, None]:
    """Démarre un container pgvector/pgvector:pg16 et yield l'URL psycopg2."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers non installé")

    try:
        container = (
            PostgresContainer("pgvector/pgvector:pg16")
            .with_env("POSTGRES_DB", "malaykaa_test")
        )
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker indisponible : {exc}")

    try:
        # testcontainers expose une URL psycopg2-compatible via get_connection_url()
        # mais elle préfixe parfois "postgresql+psycopg2" — on normalise.
        url = container.get_connection_url()
        if url.startswith("postgresql+psycopg2"):
            psyco_url = url
        else:
            psyco_url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        yield psyco_url
    finally:
        container.stop()


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_db(postgres_url: str) -> Generator[None, None, None]:
    """Pointe l'app sur le container, active pgvector, crée toutes les tables."""
    os.environ["DATABASE_URL"] = postgres_url

    # Reset le cache settings pour qu'il relise DATABASE_URL
    from app.core.config import get_settings
    get_settings.cache_clear()

    from sqlalchemy import create_engine, text

    from app.core.database import Base
    # Import nécessaire pour enregistrer tous les modèles dans Base.metadata
    import app.models  # noqa: F401

    engine = create_engine(postgres_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    engine.dispose()

    yield


# ── App + override get_db ───────────────────────────────────────────────────


@pytest.fixture()
def app(_bootstrap_db):
    """Importe l'app FastAPI et override get_db pour utiliser le container."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.database import get_db
    from app.main import app as fastapi_app

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _get_test_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = _get_test_db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()
    engine.dispose()


@pytest_asyncio.fixture()
async def client(app) -> AsyncGenerator:
    """httpx.AsyncClient branché sur l'app via ASGITransport (pas de network)."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ── Helpers DB ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db_session(app):
    """Session SQLAlchemy directe pour les fixtures de seed."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def make_user(db_session):
    """Factory : crée un User authentifié + Profile, retourne (user_id, access_token)."""
    from app.core.security import create_access_token, hash_password
    from app.models.user import PrimaryRole, Profile, User, UserRole

    created_ids: list[uuid.UUID] = []

    def _make(
        email: str | None = None,
        phone: str | None = None,
        domain: str = "Informatique",
    ) -> tuple[uuid.UUID, str]:
        suffix = uuid.uuid4().hex[:8]
        email = email or f"u_{suffix}@example.com"
        user = User(
            email=email,
            phone=phone,
            password_hash=hash_password("password123"),
            role=UserRole.b2c,
            is_active=True,
        )
        db_session.add(user)
        db_session.flush()

        profile = Profile(
            user_id=user.id,
            first_name="Test",
            last_name="User",
            country="Sénégal",
            primary_role=PrimaryRole.student.value,
            domain=domain,
        )
        db_session.add(profile)
        db_session.commit()

        created_ids.append(user.id)
        return user.id, create_access_token(str(user.id))

    yield _make
    # Pas de cleanup explicite : container session-scoped, données isolées par UUIDs.
