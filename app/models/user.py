import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserRole(str, enum.Enum):
    b2c = "b2c"
    admin = "admin"


class PrimaryRole(str, enum.Enum):
    """Rôle principal déclaré par l'utilisateur lors de l'onboarding."""
    job_seeker = "job_seeker"      # Demandeur d'emploi
    student = "student"            # Étudiant
    professional = "professional"  # Professionnel


class Gender(str, enum.Enum):
    M = "M"
    F = "F"
    other = "other"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.b2c)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Consentement explicite — Loi n° 2013-450 du 19 juin 2013 (CI), art. 6
    consent_given_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    consent_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Relations
    profile: Mapped["Profile"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    goals: Mapped[list["Goal"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    threads: Mapped[list["ChatThread"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    documents: Mapped[list["Document"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    user_opportunities: Mapped[list["UserOpportunity"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    notifications: Mapped[list["UserNotification"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821


class Profile(Base):
    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gender: Mapped[str | None] = mapped_column(
        Enum(Gender, name="gender_enum", create_constraint=False), nullable=True
    )
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    primary_role: Mapped[str | None] = mapped_column(
        Enum(PrimaryRole, name="primary_role_enum", create_constraint=False), nullable=True
    )
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True, default="fr")
    domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    field_of_study: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_status: Mapped[str | None] = mapped_column(String(200), nullable=True)
    skills: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    goals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    preferred_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cv_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Préférences de matching automatique (cron). NULL → défaut applicatif
    # (6 h). Voir app/services/matching/match_runner.py.
    match_frequency_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    match_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relation
    user: Mapped["User"] = relationship(back_populates="profile")
