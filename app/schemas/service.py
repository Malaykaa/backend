"""Schémas de la mise en relation prestataires ↔ clients.

Deux vues distinctes d'un même rapprochement, et c'est délibéré :

- `MatchCardResponse`  — ce que le CLIENT voit. L'identité et les coordonnées
  n'y figurent qu'une fois la double validation acquise.
- `ProviderInboxItem`  — ce que le PRESTATAIRE voit. La demande, jamais
  l'identité du client tant qu'il n'a pas accepté.

Cette asymétrie est la traduction en code de la règle produit : personne ne
voit les coordonnées de personne avant que les deux aient dit oui.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.service import (
    DeliveryMode, MatchDecision, MatchSource, ProviderStatus, RequestStatus, RequestType,
)


# ── Vitrine prestataire ─────────────────────────────────────────────────────


class ProviderUpsert(BaseModel):
    """Création ou mise à jour de sa propre vitrine."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=300,
                       description="Intitulé court, ex. « Plombier · dépannage 24h ».")
    description: str = Field(min_length=20, max_length=5000)
    keywords: list[str] = Field(default_factory=list, max_length=15)
    delivery_mode: DeliveryMode = Field(
        description="À distance, en présentiel ou hybride — conditionne la pertinence de la ville.",
    )
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    rate_text: str | None = Field(default=None, max_length=200)
    availability_text: str | None = Field(default=None, max_length=200)
    years_experience: int | None = Field(default=None, ge=0, le=70)
    contact_phone: str | None = Field(default=None, max_length=30)


class ProviderPublishRequest(BaseModel):
    """Publication — exige un consentement explicite, distinct de l'inscription."""

    model_config = ConfigDict(extra="forbid")

    consent_public: bool = Field(
        description="L'utilisateur accepte que sa vitrine soit visible et proposée à des tiers.",
    )


class ProviderResponse(BaseModel):
    """Vue complète — réservée au propriétaire de la vitrine."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    delivery_mode: DeliveryMode = DeliveryMode.onsite
    city: str | None = None
    country: str | None = None
    rate_text: str | None = None
    availability_text: str | None = None
    years_experience: int | None = None
    contact_phone: str | None = None
    status: ProviderStatus
    published_at: datetime | None = None
    created_at: datetime


class ProviderPublicCard(BaseModel):
    """Vue publique — ce qu'un client voit d'un prestataire.

    Ni téléphone ni identité civile : le contact ne se débloque qu'après la
    double validation, via l'endpoint dédié.
    """

    provider_id: uuid.UUID | None = None
    display_name: str = Field(description="Prénom + initiale, jamais le nom complet.")
    title: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    delivery_mode: DeliveryMode = DeliveryMode.onsite
    city: str | None = None
    country: str | None = None
    rate_text: str | None = None
    availability_text: str | None = None
    years_experience: int | None = None


# ── Demandes ────────────────────────────────────────────────────────────────


class RequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: RequestType = RequestType.prestation
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=10, max_length=5000)
    keywords: list[str] = Field(default_factory=list, max_length=15)
    delivery_mode: DeliveryMode = Field(
        description="À distance, en présentiel ou hybride — conditionne la pertinence de la ville.",
    )
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    budget_hint: str | None = Field(default=None, max_length=200)


class MatchCardResponse(BaseModel):
    """Un rapprochement, vu par le client."""

    id: uuid.UUID
    decision: MatchDecision
    source: MatchSource
    match_score: float | None = None
    notified_at: datetime
    # Absent tant que le destinataire vient du grand public et n'a pas accepté :
    # ces utilisateurs n'ont pas consenti à être exposés.
    card: ProviderPublicCard | None = None
    # Renseigné uniquement après mise en relation.
    contact_phone: str | None = None


class RequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_type: RequestType
    title: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    delivery_mode: DeliveryMode = DeliveryMode.onsite
    city: str | None = None
    country: str | None = None
    budget_hint: str | None = None
    status: RequestStatus
    published_public_at: datetime | None = None
    created_at: datetime


class RequestDetailResponse(RequestResponse):
    """Vue détaillée — les rapprochements groupés par état d'avancement.

    Le regroupement est fait côté serveur pour que l'interface n'ait aucune
    logique métier à réimplémenter, et que les trois listes restent cohérentes
    entre elles.
    """

    accepted: list[MatchCardResponse] = Field(
        default_factory=list, description="Ont accepté — en attente de votre validation.",
    )
    pending: list[MatchCardResponse] = Field(
        default_factory=list, description="Sollicités, sans réponse à ce jour.",
    )
    connected: list[MatchCardResponse] = Field(
        default_factory=list, description="Mise en relation établie — coordonnées visibles.",
    )
    declined_count: int = Field(
        default=0, description="Refus, agrégés : les détailler n'apporte rien au client.",
    )
    can_go_public: bool = Field(
        description="Vrai si la demande peut encore être élargie au grand public.",
    )


# ── Boîte de réception prestataire ──────────────────────────────────────────


class ProviderInboxItem(BaseModel):
    """Une demande reçue, vue par le prestataire.

    L'identité du client n'apparaît pas : la symétrie est volontaire, il n'a
    pas plus de raison d'être exposé que le prestataire.
    """

    match_id: uuid.UUID
    request_id: uuid.UUID
    request_type: RequestType
    title: str
    description: str
    delivery_mode: DeliveryMode = DeliveryMode.onsite
    city: str | None = None
    country: str | None = None
    budget_hint: str | None = None
    match_score: float | None = None
    decision: MatchDecision
    notified_at: datetime
    # Renseigné une fois la mise en relation établie.
    client_display_name: str | None = None
    client_phone: str | None = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accept: bool
