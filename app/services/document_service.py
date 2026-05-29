"""Service documents — génération de contenu via LLM + export PDF."""

from __future__ import annotations

import io
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.agents.base import AgentContext, AgentResponse
from app.agents.orchestrator import Orchestrator
from app.agents.types import GoalType
from app.core.exceptions import NotFoundError
from app.llm import get_llm_provider
from app.models.attachment import Attachment
from app.models.document import Document, DocumentType
from app.repositories.document_repo import DocumentRepository

ATTACHMENTS_DIR = Path(os.getenv("ATTACHMENTS_DIR", "storage/attachments"))
_MAX_EXTRACTED_CHARS = 12_000  # ~3 000 tokens — limite raisonnable pour injection LLM


class DocumentService:
    def __init__(self, db: Session) -> None:
        self.repo = DocumentRepository(db)

    async def generate(
        self,
        user_id: uuid.UUID,
        doc_type: DocumentType,
        profile: dict | None = None,
        goal_id: uuid.UUID | None = None,
        instructions: str | None = None,
    ) -> Document:
        """Génère un document via l'agent document et le sauvegarde en DB."""
        message = instructions or f"Génère un {doc_type.value} professionnel."

        ctx = AgentContext(
            user_id=user_id,
            message=message,
            profile=profile or {},
            goal_type=GoalType.DOCUMENT,
            goal_context={"document_type": doc_type.value},
        )

        llm = get_llm_provider()
        orchestrator = Orchestrator(llm)
        try:
            agent_response = await orchestrator.route(ctx)
        except Exception as exc:
            logger.error("Orchestration failed for document generation (%s): %s", doc_type.value, exc)
            agent_response = AgentResponse(
                explanation="Désolé, je n'ai pas pu générer ton document. Réessaie dans quelques instants.",
                agent_id="error",
            )

        # Le contenu du document est dans deliverables[0] ou dans explanation
        content = agent_response.explanation
        if agent_response.deliverables:
            content = agent_response.deliverables[0]

        doc = self.repo.create_document(
            user_id=user_id,
            doc_type=doc_type,
            content=content,
            goal_id=goal_id,
        )
        return doc

    def get_document(self, doc_id: uuid.UUID, user_id: uuid.UUID) -> Document:
        doc = self.repo.get_by_id(doc_id)
        if not doc or doc.user_id != user_id:
            raise NotFoundError("Document")
        return doc

    def list_documents(self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[Document]:
        return self.repo.get_user_documents(user_id, limit=limit, offset=offset)

    @staticmethod
    def extract_pdf_text(data: bytes) -> str | None:
        """Extrait le texte d'un PDF. Retourne None en cas d'échec."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            parts: list[str] = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text.strip())
            full = "\n\n".join(parts)
            return full[:_MAX_EXTRACTED_CHARS] if full else None
        except Exception as exc:
            logger.warning("PDF text extraction failed: %s", exc)
            return None

    def create_attachment(
        self,
        message_id: uuid.UUID,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> Attachment:
        """Sauvegarde un fichier sur disque et crée un Attachment lié à un message."""
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        storage_key = f"{uuid.uuid4().hex}_{filename}"
        file_path = ATTACHMENTS_DIR / storage_key
        file_path.write_bytes(data)

        extracted_text = None
        if content_type == "application/pdf":
            extracted_text = self.extract_pdf_text(data)

        attachment = Attachment(
            message_id=message_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            storage_key=storage_key,
            extracted_text=extracted_text,
        )
        self.repo.db.add(attachment)
        self.repo.db.flush()
        return attachment

    def create_pending_attachment(
        self,
        user_id: uuid.UUID,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> Attachment:
        """Upload "pending" — crée un Attachment sans message_id (lié plus tard au message)."""
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        storage_key = f"{uuid.uuid4().hex}_{filename}"
        file_path = ATTACHMENTS_DIR / storage_key
        file_path.write_bytes(data)

        extracted_text = None
        if content_type == "application/pdf":
            extracted_text = self.extract_pdf_text(data)

        attachment = Attachment(
            message_id=None,
            pending_user_id=user_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            storage_key=storage_key,
            extracted_text=extracted_text,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        self.repo.db.add(attachment)
        self.repo.db.flush()
        return attachment

    def link_attachments_to_message(
        self,
        attachment_ids: list[uuid.UUID],
        message_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[Attachment]:
        """Lie des attachments pending à un message. Vérifie l'ownership."""
        linked: list[Attachment] = []
        for att_id in attachment_ids:
            att = self.repo.db.get(Attachment, att_id)
            if not att:
                continue
            if att.pending_user_id != user_id:
                continue
            att.message_id = message_id
            att.pending_user_id = None
            att.expires_at = None
            linked.append(att)
        return linked

    def get_attachment(self, attachment_id: uuid.UUID) -> Attachment:
        attachment = self.repo.db.get(Attachment, attachment_id)
        if not attachment:
            raise NotFoundError("Attachment")
        return attachment

    def get_attachment_path(self, attachment: Attachment) -> Path:
        return ATTACHMENTS_DIR / attachment.storage_key

    @staticmethod
    def export_pdf(doc: Document) -> bytes:
        """Exporte le contenu du document en PDF via reportlab."""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        buffer = io.BytesIO()
        pdf = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=2 * cm,
            rightMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Heading1"],
            fontSize=16,
            spaceAfter=12,
        )
        body_style = ParagraphStyle(
            "DocBody",
            parent=styles["Normal"],
            fontSize=11,
            leading=15,
            spaceAfter=8,
        )

        story = []

        # Titre
        type_labels = {
            "cv": "Curriculum Vitae",
            "cover_letter": "Lettre de Motivation",
            "business_plan": "Business Plan",
            "pitch_deck": "Pitch Deck",
            "email_pro": "Email Professionnel",
            "report": "Rapport",
            "study_sheet": "Fiche de Révision",
            "contract": "Contrat",
        }
        title = type_labels.get(doc.type.value, "Document")
        story.append(Paragraph(title, title_style))
        story.append(Spacer(1, 0.5 * cm))

        # Contenu — convertir le markdown simplifié en paragraphes
        for line in doc.content.split("\n"):
            line = line.strip()
            if not line:
                story.append(Spacer(1, 0.3 * cm))
                continue

            # Titres markdown
            if line.startswith("### "):
                style = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=12, spaceAfter=6)
                story.append(Paragraph(line[4:], style))
            elif line.startswith("## "):
                style = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, spaceAfter=8)
                story.append(Paragraph(line[3:], style))
            elif line.startswith("# "):
                story.append(Paragraph(line[2:], title_style))
            elif line.startswith("- ") or line.startswith("* "):
                bullet_text = f"• {line[2:]}"
                story.append(Paragraph(bullet_text, body_style))
            elif line.startswith("**") and line.endswith("**"):
                bold_style = ParagraphStyle("Bold", parent=body_style, fontName="Helvetica-Bold")
                story.append(Paragraph(line.strip("*"), bold_style))
            else:
                story.append(Paragraph(line, body_style))

        pdf.build(story)
        return buffer.getvalue()
