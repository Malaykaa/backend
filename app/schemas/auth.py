"""SchÃ©mas Pydantic pour l'authentification."""

import re
import uuid
from datetime import datetime, timezone

from typing import Annotated

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import Gender, PrimaryRole, UserRole

MIN_AGE = 16


# â”€â”€ RequÃªtes â€” email â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class RegisterRequest(BaseModel):
    """Inscription par email â€” accepte snake_case ET camelCase (frontend React)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str | None = Field(
        default=None, max_length=100,
        validation_alias=AliasChoices("firstName", "first_name"),
    )
    last_name: str | None = Field(
        default=None, max_length=100,
        validation_alias=AliasChoices("lastName", "last_name"),
    )
    phone: str | None = Field(default=None, max_length=30)
    country: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=10)
    role: str | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str


# â”€â”€ RequÃªtes â€” phone OTP â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class RegisterPhoneRequest(BaseModel):
    """Inscription par numéro WhatsApp — sans vérification OTP.

    Le numéro est auto-déclaré : l'utilisateur le renseigne pour recevoir les
    opportunités sur WhatsApp, ce qui l'incite naturellement à indiquer son
    vrai numéro (aucune vérification de propriété n'est effectuée).
    """

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=30)
    password: str = Field(min_length=8, max_length=128)

    # Données profil optionnelles (récoltées pendant l'onboarding)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    gender: Gender | None = None
    birth_year: int | None = Field(default=None, ge=1920)
    country: str | None = Field(default=None, max_length=100)
    primary_role: PrimaryRole | None = None

    @field_validator("phone")
    @classmethod
    def phone_must_contain_digits(cls, v: str) -> str:
        cleaned = re.sub(r"\D", "", v)
        if len(cleaned) < 8:
            raise ValueError("NumÃ©ro de tÃ©lÃ©phone invalide (min 8 chiffres).")
        return v

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, v: str | None) -> str | None:
        return {"male": "M", "female": "F"}.get(v, v) if v is not None else v

    @field_validator("primary_role", mode="before")
    @classmethod
    def normalize_primary_role(cls, v: str | None) -> str | None:
        return {"jobseeker": "job_seeker"}.get(v, v) if v is not None else v

    @field_validator("birth_year")
    @classmethod
    def birth_year_min_age(cls, v: int | None) -> int | None:
        if v is not None:
            max_year = datetime.now(timezone.utc).year - MIN_AGE
            if v > max_year:
                raise ValueError(f"Vous devez avoir au moins {MIN_AGE} ans (annÃ©e max : {max_year}).")
        return v


class LoginPhoneRequest(BaseModel):
    """Connexion par téléphone + mot de passe."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=30)
    password: str


class ChangePasswordRequest(BaseModel):
    """Changement de mot de passe authentifié."""

    model_config = ConfigDict(extra="forbid")

    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class CheckPhoneRequest(BaseModel):
    """Vérifie si un numéro est déjà associé à un compte."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=30)

    @field_validator("phone")
    @classmethod
    def phone_must_contain_digits(cls, v: str) -> str:
        cleaned = re.sub(r"\D", "", v)
        if len(cleaned) < 8:
            raise ValueError("Numéro de téléphone invalide (min 8 chiffres).")
        return v


class ForgotPasswordRequest(BaseModel):
    """Demande de réinitialisation de mot de passe via OTP WhatsApp."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=30)

    @field_validator("phone")
    @classmethod
    def phone_must_contain_digits(cls, v: str) -> str:
        cleaned = re.sub(r"\D", "", v)
        if len(cleaned) < 8:
            raise ValueError("Numéro de téléphone invalide (min 8 chiffres).")
        return v


class ResetPasswordRequest(BaseModel):
    """Réinitialisation du mot de passe après vérification OTP."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=30)
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("code")
    @classmethod
    def code_must_be_digits(cls, v: str) -> str:
        if not re.fullmatch(r"\d{6}", v):
            raise ValueError("Le code doit contenir exactement 6 chiffres.")
        return v


# â”€â”€ RÃ©ponses â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    gender: str | None = None
    birth_year: int | None = None
    country: str | None = None
    city: str | None = None
    language: str | None = None
    primary_role: str | None = None
    domain: str | None = None
    field_of_study: str | None = None
    current_status: str | None = None
    skills: dict | None = None
    goals: dict | list | None = None
    preferred_content: str | None = None
    cv_url: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime
    profile: ProfileResponse | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# â”€â”€ RÃ©ponses compatibles frontend â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class NestLikeUser(BaseModel):
    """Format utilisateur attendu par le frontend React."""
    id: str
    email: str | None = None
    phone: str | None = None
    role: str = "b2c"
    is_active: bool = True
    created_at: datetime


class AuthResultResponse(BaseModel):
    """RÃ©ponse auth compatible frontend: accessToken + user."""
    accessToken: str  # noqa: N815
    user: NestLikeUser


class MeResponse(BaseModel):
    """RÃ©ponse GET /auth/me compatible frontend."""
    user: NestLikeUser


class WrappedProfileResponse(BaseModel):
    """RÃ©ponse profil wrappÃ©e compatible frontend."""
    profile: ProfileResponse | None = None


