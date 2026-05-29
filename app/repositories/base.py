"""Repository de base générique — seule couche qui parle à la DB.

Deux variantes cohabitent :
- BaseRepository      : sync, Session psycopg2 — APScheduler, Alembic, `def` routers
- AsyncBaseRepository : async, AsyncSession asyncpg — `async def` routers SSE critiques
"""

import uuid
from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """CRUD générique pour tout modèle SQLAlchemy (sync)."""

    def __init__(self, model: type[ModelT], db: Session) -> None:
        self.model = model
        self.db = db

    def get_by_id(self, id: uuid.UUID) -> ModelT | None:
        return self.db.get(self.model, id)

    def create(self, **kwargs) -> ModelT:
        instance = self.model(**kwargs)
        self.db.add(instance)
        self.db.flush()
        return instance

    def save(self, instance: ModelT) -> ModelT:
        self.db.add(instance)
        self.db.flush()
        return instance

    def delete(self, instance: ModelT) -> None:
        self.db.delete(instance)
        self.db.flush()


class AsyncBaseRepository(Generic[ModelT]):
    """CRUD générique pour tout modèle SQLAlchemy (async — asyncpg)."""

    def __init__(self, model: type[ModelT], db: AsyncSession) -> None:
        self.model = model
        self.db = db

    async def get_by_id(self, id: uuid.UUID) -> ModelT | None:
        return await self.db.get(self.model, id)

    async def create(self, **kwargs) -> ModelT:
        instance = self.model(**kwargs)
        self.db.add(instance)
        await self.db.flush()
        return instance

    async def save(self, instance: ModelT) -> ModelT:
        self.db.add(instance)
        await self.db.flush()
        return instance

    async def delete(self, instance: ModelT) -> None:
        await self.db.delete(instance)
        await self.db.flush()
