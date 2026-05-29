"""Moteur async SQLAlchemy — driver asyncpg.

Utilisé par les routers FastAPI critiques (SSE chat, action_adapter)
pour ne jamais bloquer la boucle asyncio pendant les I/O PostgreSQL.

Le moteur sync (database.py) reste en place pour :
- Alembic (migrations)
- APScheduler / MatchRunner (tâches périodiques)
- /health/ready (probe de disponibilité)
- Tous les `def` handlers de router (FastAPI les exécute dans un threadpool)
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings

settings = get_settings()


def _to_async_url(url: str) -> str:
    """Remplace le driver psycopg2 par asyncpg dans l'URL de connexion."""
    return (
        url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
           .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


async_engine = create_async_engine(
    _to_async_url(settings.database_url),
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  # obligatoire : évite le lazy-load hors session
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Dépendance FastAPI — injecte une AsyncSession dans les `async def` handlers."""
    async with AsyncSessionLocal() as session:
        yield session
