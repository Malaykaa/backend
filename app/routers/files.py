"""Routes fichiers — upload de pièces jointes + téléchargement."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.services.document_service import DocumentService

router = APIRouter(prefix="/files", tags=["files"])

_ALLOWED_TYPES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


class UploadedFileResponse(BaseModel):
    attachment_id: str
    storage_key: str
    filename: str
    content_type: str
    extracted: bool  # True si du texte a été extrait (PDF)


@router.post("/upload", response_model=UploadedFileResponse, status_code=201)
async def upload_file(
    file: UploadFile,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Upload un fichier avant envoi de message. Retourne un attachment_id à passer avec le message."""
    if file.content_type not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Type de fichier non supporté : {file.content_type}. Types acceptés : PDF, TXT, DOCX, images.",
        )

    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 10 Mo).")
    if not data:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    svc = DocumentService(db)
    attachment = svc.create_pending_attachment(
        user_id=current_user.id,
        filename=file.filename or "upload",
        content_type=file.content_type,
        data=data,
    )
    db.commit()
    db.refresh(attachment)

    return UploadedFileResponse(
        attachment_id=str(attachment.id),
        storage_key=attachment.storage_key,
        filename=attachment.filename,
        content_type=attachment.content_type,
        extracted=attachment.extracted_text is not None,
    )


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
    if not message or message.thread.user_id != current_user.id:
        raise NotFoundError("Attachment")

    file_path = service.get_attachment_path(attachment)
    if not file_path.exists():
        raise NotFoundError("Attachment")

    return FileResponse(
        path=str(file_path),
        filename=attachment.filename,
        media_type=attachment.content_type,
    )
