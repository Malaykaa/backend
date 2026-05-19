"""Repository documents — accès aux documents générés."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentType
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    def __init__(self, db: Session) -> None:
        super().__init__(Document, db)

    def create_document(
        self,
        user_id: uuid.UUID,
        doc_type: DocumentType,
        content: str,
        goal_id: uuid.UUID | None = None,
    ) -> Document:
        return self.create(
            user_id=user_id,
            type=doc_type,
            content=content,
            goal_id=goal_id,
        )

    def get_user_documents(self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    def set_export_url(self, doc: Document, url: str) -> Document:
        doc.export_url = url
        self.db.flush()
        return doc
