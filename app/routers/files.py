"""Routes fichiers — téléchargement des pièces jointes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.services.document_service import DocumentService

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/attachments/{attachment_id}")
def download_attachment(
    attachment_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = DocumentService(db)
    attachment = service.get_attachment(attachment_id)

    # Vérifier que l'utilisateur a accès (via le message → thread → user)
    message = attachment.message
    if message.thread.user_id != current_user.id:
        raise NotFoundError("Attachment")

    file_path = service.get_attachment_path(attachment)
    if not file_path.exists():
        raise NotFoundError("Attachment")

    return FileResponse(
        path=str(file_path),
        filename=attachment.filename,
        media_type=attachment.content_type,
    )
