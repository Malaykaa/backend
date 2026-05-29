"""Routes documents — génération et export PDF."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import extract_profile, get_current_user
from app.models.user import User
from app.schemas.document import DocumentGenerateRequest, DocumentResponse
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/generate", response_model=DocumentResponse, status_code=201)
async def generate_document(
    body: DocumentGenerateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = DocumentService(db)
    profile = extract_profile(current_user)

    doc = await service.generate(
        user_id=current_user.id,
        doc_type=body.type,
        profile=profile,
        goal_id=body.goal_id,
        instructions=body.instructions,
    )
    db.commit()
    return doc


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    service = DocumentService(db)
    return service.list_documents(current_user.id, limit=limit, offset=offset)


@router.get("/{doc_id}", response_model=DocumentResponse)
def get_document(
    doc_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = DocumentService(db)
    return service.get_document(doc_id, current_user.id)


@router.get("/{doc_id}/export")
def export_document_pdf(
    doc_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = DocumentService(db)
    doc = service.get_document(doc_id, current_user.id)

    pdf_bytes = DocumentService.export_pdf(doc)

    filename = f"malayka_{doc.type.value}_{str(doc.id)[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
