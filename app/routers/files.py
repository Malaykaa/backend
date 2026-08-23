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
_CHUNK_BYTES = 1024 * 1024     # lecture par tranches de 1 Mo

# Signatures (magic bytes) des formats qui en possedent une stable, en hexa pour
# rester lisibles. Les formats sans signature fiable (texte brut, CSV) ne sont
# volontairement pas listes : on ne rejette que ce qu'on peut refuter, jamais ce
# qu'on ne sait pas verifier.
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (bytes.fromhex("255044462d"),),          # %PDF-
    "image/jpeg":      (bytes.fromhex("ffd8ff"),),
    "image/png":       (bytes.fromhex("89504e470d0a1a0a"),),
    "image/gif":       (b"GIF87a", b"GIF89a"),
    # DOCX est un conteneur ZIP.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        bytes.fromhex("504b0304"),
    ),
}


def _signature_matches(data: bytes, content_type: str | None) -> bool:
    """Vrai si le contenu correspond au type declare, ou si ce type n'a pas de
    signature verifiable (on n'invente pas de refus)."""
    if content_type == "image/webp":
        # RIFF....WEBP — la taille s'intercale entre les deux marqueurs.
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    expected = _MAGIC_BYTES.get(content_type or "")
    if not expected:
        return True
    return any(data.startswith(sig) for sig in expected)


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

    # Lecture par morceaux : `await file.read()` chargeait TOUT le fichier en
    # memoire avant de verifier sa taille. Un envoi volontairement enorme pouvait
    # donc saturer la memoire du serveur malgre la limite affichee de 10 Mo.
    # On s'arrete des le depassement, sans jamais accumuler plus que la limite.
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > _MAX_BYTES:
            raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 10 Mo).")
        chunks.append(chunk)
    data = b"".join(chunks)

    if not data:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    # Le content_type est declare par le client : il ne prouve rien. On verifie
    # la signature reelle des formats qui en ont une, pour qu'un executable ne
    # puisse pas etre stocke en se presentant comme une image.
    if not _signature_matches(data, file.content_type):
        raise HTTPException(
            status_code=415,
            detail=f"Le contenu du fichier ne correspond pas au type declare ({file.content_type}).",
        )

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
