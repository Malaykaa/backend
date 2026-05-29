"""Repository utilisateur — accès data User et Profile.

Deux variantes :
- UserRepository      : sync (APScheduler, `def` routers)
- AsyncUserRepository : async (routers `async def` — auth, deps)
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.user import Profile, User
from app.repositories.base import AsyncBaseRepository, BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, db: Session) -> None:
        super().__init__(User, db)

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return self.db.scalars(stmt).first()

    def get_by_phone(self, phone: str) -> User | None:
        """Recherche par numéro de téléphone."""
        stmt = select(User).where(User.phone == phone)
        return self.db.scalars(stmt).first()

    def get_with_profile(self, user_id: uuid.UUID) -> User | None:
        stmt = (
            select(User)
            .options(joinedload(User.profile))
            .where(User.id == user_id)
        )
        return self.db.scalars(stmt).first()

    def create_with_profile(
        self,
        password_hash: str,
        email: str | None = None,
        phone: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        gender: str | None = None,
        birth_year: int | None = None,
        country: str | None = None,
        primary_role: str | None = None,
    ) -> User:
        user = User(email=email, password_hash=password_hash, phone=phone)
        self.db.add(user)
        self.db.flush()

        profile = Profile(
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            gender=gender,
            birth_year=birth_year,
            country=country,
            primary_role=primary_role,
        )
        self.db.add(profile)
        self.db.flush()

        user.profile = profile
        return user

    def update_profile(self, user_id: uuid.UUID, **kwargs) -> Profile | None:
        stmt = select(Profile).where(Profile.user_id == user_id)
        profile = self.db.scalars(stmt).first()
        if not profile:
            return None
        for key, value in kwargs.items():
            setattr(profile, key, value)
        self.db.flush()
        return profile


# ── Variante async (asyncpg) ────────────────────────────────────────────────


class AsyncUserRepository(AsyncBaseRepository[User]):
    """Variante async de UserRepository pour les dépendances d'authentification."""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(User, db)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalars().first()

    async def get_by_phone(self, phone: str) -> User | None:
        result = await self.db.execute(select(User).where(User.phone == phone))
        return result.scalars().first()

    async def get_with_profile(self, user_id: uuid.UUID) -> User | None:
        stmt = (
            select(User)
            .options(selectinload(User.profile))
            .where(User.id == user_id)
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def create_with_profile(
        self,
        password_hash: str,
        email: str | None = None,
        phone: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        gender: str | None = None,
        birth_year: int | None = None,
        country: str | None = None,
        primary_role: str | None = None,
    ) -> User:
        user = User(email=email, password_hash=password_hash, phone=phone)
        self.db.add(user)
        await self.db.flush()

        profile = Profile(
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            gender=gender,
            birth_year=birth_year,
            country=country,
            primary_role=primary_role,
        )
        self.db.add(profile)
        await self.db.flush()

        user.profile = profile
        return user
