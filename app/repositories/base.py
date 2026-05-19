"""Repository de base générique — seule couche qui parle à la DB."""

import uuid
from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from app.core.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """CRUD générique pour tout modèle SQLAlchemy."""

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
