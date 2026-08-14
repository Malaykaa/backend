"""Schémas du backoffice Services.

Vue administrateur — délibérément plus complète que celle des utilisateurs :
l'admin voit les identités et les coordonnées sans attendre la double
validation, parce que son rôle est justement de modérer et de diagnostiquer.
Cette asymétrie est assumée et documentée, pas accidentelle.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.service import (
    MatchDecision, MatchSource, ProviderStatus, RequestStatus, RequestType,
)


class ServiceFunnel(BaseModel):
    """Entonnoir de conversion — c'est lui qui dit si la place de marché vit.

    Une demande sans sollicitation signale un problème de matching ;
    des sollicitations sans acceptation, un problème de pertinence ou
    d'engagement des prestataires ; des acceptations sans mise en relation,
    un problème de qualité des profils proposés.
    """

    requests_total: int
    requests_with_matches: int
    matches_total: int
    provider_accepted: int
    connected: int
    provider_response_rate: float = Field(description="Part des sollicitations ayant reçu une réponse, en %.")
    acceptance_rate: float = Field(description="Part des sollicitations acceptées par le prestataire, en %.")
    connection_rate: float = Field(description="Part des acceptations transformées en mise en relation, en %.")


class ServiceBucket(BaseModel):
    key: str
    label: str
    count: int
    pct: float = Field(default=0.0, description="Part du total de la dimension, en %.")


class AdminServiceStats(BaseModel):
    providers_total: int
    providers_published: int
    providers_draft: int
    providers_suspended: int
    requests_total: int
    requests_open: int
    requests_public: int
    requests_fulfilled: int
    funnel: ServiceFunnel
    requests_by_type: list[ServiceBucket]
    providers_by_country: list[ServiceBucket]
    requests_by_country: list[ServiceBucket]
    monthly_requests: list[dict]
    monthly_providers: list[dict]
    avg_match_score: float | None = Field(
        default=None, description="Score moyen des rapprochements — indicateur de qualité du matching.",
    )
    unmatched_requests: int = Field(
        description="Demandes n'ayant déclenché aucune sollicitation. Le signal le plus alarmant.",
    )


class AdminProviderItem(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None = None
    user_phone: str | None = None
    display_name: str | None = None
    title: str
    keywords: list[str] = Field(default_factory=list)
    city: str | None = None
    country: str | None = None
    rate_text: str | None = None
    status: ProviderStatus
    has_embedding: bool
    received_count: int = Field(description="Sollicitations reçues.")
    accepted_count: int = Field(description="Sollicitations acceptées.")
    connected_count: int = Field(description="Mises en relation obtenues.")
    published_at: datetime | None = None
    created_at: datetime


class AdminProviderDetail(AdminProviderItem):
    description: str
    availability_text: str | None = None
    years_experience: int | None = None
    contact_phone: str | None = None
    consent_public_at: datetime | None = None


class AdminMatchItem(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str | None = None
    provider_title: str | None = Field(
        default=None, description="Null si le destinataire vient du grand public.",
    )
    source: MatchSource
    decision: MatchDecision
    match_score: float | None = None
    match_mode: str | None = None
    notified_at: datetime
    provider_responded_at: datetime | None = None
    client_responded_at: datetime | None = None


class AdminRequestItem(BaseModel):
    id: uuid.UUID
    requester_id: uuid.UUID
    requester_name: str | None = None
    request_type: RequestType
    title: str
    city: str | None = None
    country: str | None = None
    budget_hint: str | None = None
    status: RequestStatus
    has_embedding: bool
    matches_count: int
    accepted_count: int
    connected_count: int
    published_public_at: datetime | None = None
    created_at: datetime


class AdminRequestDetail(AdminRequestItem):
    description: str
    keywords: list[str] = Field(default_factory=list)
    matches: list[AdminMatchItem] = Field(default_factory=list)


class AdminProviderModerate(BaseModel):
    """Action de modération.

    `suspended` est réservé à l'administrateur : le prestataire ne peut pas
    s'y remettre lui-même, contrairement à `paused` qu'il contrôle.
    """

    status: ProviderStatus
