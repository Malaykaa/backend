import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DocumentType(str, enum.Enum):
    cv = "cv"
    cover_letter = "cover_letter"
    business_plan = "business_plan"
    pitch_deck = "pitch_deck"
    email_pro = "email_pro"
    report = "report"
    study_sheet = "study_sheet"
    contract = "contract"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    export_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relations
    user: Mapped["User"] = relationship(back_populates="documents")  # noqa: F821
    goal: Mapped["Goal | None"] = relationship(back_populates="documents")  # noqa: F821
