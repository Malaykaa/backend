import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class GoalType(str, enum.Enum):
    exam = "exam"
    scholarship = "scholarship"
    funding = "funding"
    tender = "tender"
    study_grant = "study_grant"
    career = "career"
    freelance = "freelance"
    orientation = "orientation"
    coursework = "coursework"


class GoalStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    paused = "paused"


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[GoalType] = mapped_column(Enum(GoalType), nullable=False)
    status: Mapped[GoalStatus] = mapped_column(Enum(GoalStatus), nullable=False, default=GoalStatus.active)
    context_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relations
    user: Mapped["User"] = relationship(back_populates="goals")  # noqa: F821
    plan: Mapped["Plan | None"] = relationship(back_populates="goal", uselist=False, cascade="all, delete-orphan")  # noqa: F821
    threads: Mapped[list["ChatThread"]] = relationship(back_populates="goal", cascade="all, delete-orphan")  # noqa: F821
    documents: Mapped[list["Document"]] = relationship(back_populates="goal", cascade="all, delete-orphan")  # noqa: F821
