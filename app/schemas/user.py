"""Schémas Pydantic pour le profil utilisateur."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.user import Gender, PrimaryRole

MIN_AGE = 16


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30, exclude=True)
    gender: str | None = Field(default=None, max_length=10)
    birth_year: int | None = Field(default=None, ge=1920)
    birth_date: str | None = Field(default=None, exclude=True)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=10)
    primary_role: PrimaryRole | None = None
    domain: str | None = Field(default=None, max_length=200)
    field_of_study: str | None = Field(default=None, max_length=200)
    current_status: str | None = Field(default=None, max_length=200)
    skills: dict | None = None
    goals: dict | list | None = None
    preferred_content: str | None = Field(default=None, max_length=200)
    cv_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def convert_birth_date_to_year(cls, data: dict) -> dict:
        """Frontend envoie birth_date (ex: '2000-01-15'), on extrait l'année."""
        if isinstance(data, dict) and "birth_date" in data and "birth_year" not in data:
            bd = data["birth_date"]
            if isinstance(bd, str) and len(bd) >= 4:
                try:
                    data["birth_year"] = int(bd[:4])
                except ValueError:
                    pass
        return data

    @field_validator("birth_year")
    @classmethod
    def birth_year_min_age(cls, v: int | None) -> int | None:
        if v is not None:
            max_year = datetime.now(timezone.utc).year - MIN_AGE
            if v > max_year:
                raise ValueError(f"Vous devez avoir au moins {MIN_AGE} ans (année max : {max_year}).")
        return v
