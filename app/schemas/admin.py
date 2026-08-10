"""Schemas Pydantic pour le backoffice admin."""
from __future__ import annotations
import math
from datetime import datetime
from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict, computed_field, field_validator

T = TypeVar("T")

class AdminPaginated(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    items: list[T]
    total: int
    page: int
    size: int
    @computed_field
    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.size)) if self.size else 1

class AdminStats(BaseModel):
    users_total: int; users_new_7d: int; threads_total: int; messages_today: int
    offers_total: int; offers_active: int; documents_total: int; intents_total: int; goals_total: int

class AdminUserItem(BaseModel):
    id: str; email: str | None; phone: str | None; role: str; is_active: bool; created_at: datetime
    first_name: str | None; last_name: str | None; primary_role: str | None; country: str | None
    threads_count: int; goals_count: int; documents_count: int

class AdminUserDetail(AdminUserItem):
    domain: str | None; field_of_study: str | None; city: str | None; birth_year: int | None
    gender: str | None; language: str | None
    recent_threads: list[AdminThreadItem] = []
    recent_goals: list[AdminGoalItem] = []

class AdminUserUpdate(BaseModel):
    role: str | None = None; is_active: bool | None = None
    @field_validator("role")
    @classmethod
    def role_valid(cls, v):
        if v is not None and v not in ("b2c", "admin"): raise ValueError("role invalide")
        return v

class AdminOfferItem(BaseModel):
    id: str; source: str; offer_type: str | None; title: str; company: str | None; location: str | None
    url: str | None; quality_score: float | None; is_active: bool; has_embedding: bool
    scraped_at: datetime; posted_at: datetime | None; expires_at: datetime | None

class AdminOfferDetail(AdminOfferItem):
    description: str | None; external_id: str; normalized_title: str | None; salary: str | None

class AdminOfferUpdate(BaseModel):
    is_active: bool | None = None; quality_score: float | None = None

class AdminGoalItem(BaseModel):
    id: str; user_id: str; user_email: str | None; type: str; status: str
    preset_key: str | None; created_at: datetime; threads_count: int

class AdminThreadItem(BaseModel):
    id: str; user_id: str; user_email: str | None; title: str | None
    status: str; message_count: int; created_at: datetime

class AdminMessageItem(BaseModel):
    id: str; role: str; content: str; created_at: datetime
    agent_id: str | None; processing_ms: int | None; is_active: bool

class AdminThreadDetail(AdminThreadItem):
    messages: list[AdminMessageItem] = []

class AdminDocumentItem(BaseModel):
    id: str; user_id: str; user_email: str | None; type: str
    version: int; content_preview: str; created_at: datetime

class AdminIntentItem(BaseModel):
    id: str; user_id: str; user_email: str | None; intent_type: str | None
    intent_summary: str; domain: str | None; location: str | None; level: str | None
    keywords: list[str] | None; version: int; extracted_at: datetime

AdminUserDetail.model_rebuild()


# ── Création depuis l'admin ────────────────────────────────────────────────

class AdminOfferCreate(BaseModel):
    """Offre saisie manuellement depuis le backoffice (WhatsApp, Telegram, etc.)."""
    title: str
    offer_type: str = "opportunity"
    description: str | None = None
    url: str | None = None
    company: str | None = None
    location: str | None = None
    salary: str | None = None
    source: str = "admin"
    posted_at: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("offer_type")
    @classmethod
    def offer_type_valid(cls, v: str) -> str:
        valid = {"job","formation","grant","scholarship","partnership","call_for_applications","opportunity","resource"}
        if v not in valid:
            raise ValueError(f"offer_type invalide. Valeurs: {', '.join(sorted(valid))}")
        return v


class AdminUserCreate(BaseModel):
    """Nouvel utilisateur créé depuis le backoffice."""
    email: str | None = None
    phone: str | None = None
    password: str
    role: str = "b2c"
    first_name: str | None = None
    last_name: str | None = None
    primary_role: str | None = None
    country: str | None = None

    @field_validator("email", "phone", mode="before")
    @classmethod
    def not_empty(cls, v):
        return v or None

    def model_post_init(self, __context) -> None:
        if not self.email and not self.phone:
            raise ValueError("email ou phone est obligatoire.")


class AdminScrapingActorItem(BaseModel):
    """Actor Apify géré depuis l'admin."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    actor_id: str
    label: str
    offer_type: str
    source_name: str
    normalizer_type: str
    input_json: dict
    run_mode: str
    is_active: bool
    notes: str | None
    created_at: datetime


class AdminScrapingActorCreate(BaseModel):
    actor_id: str
    label: str
    offer_type: str = "opportunity"
    source_name: str
    normalizer_type: str = "web"
    input_json: dict = {}
    run_mode: str = "both"
    notes: str | None = None

    @field_validator("offer_type")
    @classmethod
    def offer_type_valid(cls, v: str) -> str:
        valid = {"job","formation","grant","scholarship","partnership",
                 "call_for_applications","opportunity","resource"}
        if v not in valid:
            raise ValueError(f"offer_type invalide: {', '.join(sorted(valid))}")
        return v

    @field_validator("normalizer_type")
    @classmethod
    def normalizer_valid(cls, v: str) -> str:
        valid = {"indeed","linkedin_job","linkedin_post","google_jobs","facebook","web"}
        if v not in valid:
            raise ValueError(f"normalizer invalide: {', '.join(sorted(valid))}")
        return v

    @field_validator("run_mode")
    @classmethod
    def run_mode_valid(cls, v: str) -> str:
        if v not in {"light","heavy","both"}:
            raise ValueError("run_mode doit être light, heavy ou both")
        return v


class AdminScrapingActorUpdate(BaseModel):
    label: str | None = None
    offer_type: str | None = None
    source_name: str | None = None
    normalizer_type: str | None = None
    input_json: dict | None = None
    run_mode: str | None = None
    is_active: bool | None = None
    notes: str | None = None


class AdminScrapingSourceItem(BaseModel):
    """Source web de scraping gérée depuis l'admin."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str
    label: str | None
    category: str
    notes: str | None
    is_active: bool
    created_at: datetime


class AdminScrapingSourceCreate(BaseModel):
    url: str
    label: str | None = None
    category: str = "job_boards"
    notes: str | None = None

    @field_validator("category")
    @classmethod
    def category_valid(cls, v: str) -> str:
        valid = {"job_boards", "opportunities", "grants", "scholarships", "call_for_applications"}
        if v not in valid:
            raise ValueError(f"Catégorie invalide. Valeurs: {', '.join(sorted(valid))}")
        return v


class AdminScrapingSourceUpdate(BaseModel):
    label: str | None = None
    category: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class AdminDeliverableItem(BaseModel):
    """Livrable genere par l'IA dans un thread chat (payload.deliverables)."""
    message_id: str
    thread_id: str
    thread_title: str | None
    user_id: str
    user_email: str | None
    content_preview: str
    agent_id: str | None
    created_at: datetime


class AdminStructureMember(BaseModel):
    user_id: str
    first_name: str | None
    last_name: str | None
    email: str | None
    phone: str | None
    role: str
    joined_at: datetime


class AdminStructureClassroom(BaseModel):
    id: str
    name: str
    invite_code: str
    teachers_count: int
    students_count: int
    pending_members_count: int
    courses_count: int
    created_at: datetime


class AdminStructureInvitation(BaseModel):
    id: str
    first_name: str
    last_name: str
    contact: str | None
    status: str
    created_at: datetime
    expires_at: datetime


class AdminStructureItem(BaseModel):
    """Demande de creation de structure (Malayka Institution) — file de validation."""
    id: str
    name: str
    country: str | None
    status: str
    structure_type: str | None
    address: str | None
    email: str | None
    requested_by_email: str | None
    created_at: datetime
    members_count: int


class AdminStructureDetail(AdminStructureItem):
    structure_type_other: str | None
    members: list[AdminStructureMember] = []
    classrooms: list[AdminStructureClassroom] = []
    invitations: list[AdminStructureInvitation] = []
    classrooms_count: int
    students_total: int
    courses_total: int
    pending_invitations_count: int


class AdminStructureUpdate(BaseModel):
    status: str | None = None

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in ("pending", "active", "rejected"):
            raise ValueError("statut invalide")
        return v

