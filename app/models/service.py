"""Mise en relation prestataires ↔ clients — « Proposer mes services » / « Chercher un prestataire ».

Trois entités, une règle : les coordonnées ne circulent qu'après une double
validation. Le prestataire accepte la demande, puis le client retient le
prestataire. Tant que les deux n'ont pas dit oui, personne n'a le numéro de
personne.

Deux viviers successifs, dans cet ordre :
1. Les prestataires publiés (ceux qui ont créé une vitrine). Leurs profils sont
   montrés au client dès l'envoi de la demande — ils ont accepté d'être visibles.
2. Le grand public, uniquement si le client le demande explicitement. Ces
   utilisateurs n'ont pas de vitrine publique : leur profil n'est révélé au
   client qu'APRÈS qu'ils aient accepté la demande.

Les champs `title` / `normalized_title` / `description` portent volontairement
les mêmes noms que sur ScrapedOffer : le moteur de matching lit ces attributs
par typage canard, ce qui permet de le réutiliser sans le modifier.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import HALFVEC

from app.core.database import Base
from app.models.scraped_offer import EMBEDDING_DIM


class ProviderStatus(str, enum.Enum):
    draft = "draft"          # vitrine créée, pas encore visible
    published = "published"  # visible et interrogeable par le matching
    paused = "paused"        # retirée temporairement par le prestataire
    suspended = "suspended"  # retirée par un administrateur (modération)


class RequestType(str, enum.Enum):
    """Nature du besoin — change le vocabulaire de l'interface, pas le matching."""

    prestation = "prestation"  # contrat de prestation directe
    emploi = "emploi"          # CDD / CDI
    stage = "stage"
    autre = "autre"


class RequestStatus(str, enum.Enum):
    open = "open"            # en cours, vivier prestataires uniquement
    public = "public"        # étendue au grand public à la demande du client
    fulfilled = "fulfilled"  # au moins une mise en relation établie
    closed = "closed"        # fermée par le client
    expired = "expired"


class MatchDecision(str, enum.Enum):
    """État d'un couple (demande, destinataire).

    L'ordre compte : `client_accepted` n'est atteignable que depuis
    `provider_accepted`. C'est cette contrainte qui garantit la double
    validation avant tout échange de coordonnées.
    """

    pending = "pending"                      # notifié, sans réponse
    provider_accepted = "provider_accepted"  # le prestataire se propose
    provider_declined = "provider_declined"
    client_accepted = "client_accepted"      # mise en relation établie
    client_declined = "client_declined"
    expired = "expired"


class MatchSource(str, enum.Enum):
    """Vivier d'origine — détermine ce que le client voit et à quel moment."""

    provider = "provider"  # vitrine publiée : profil visible dès l'envoi
    public = "public"      # grand public : profil révélé après acceptation


class ServiceProvider(Base):
    """Vitrine publique d'un prestataire — distincte du Profile, qui reste privé.

    Séparer les deux est délibéré : le Profile sert à recevoir des opportunités,
    cette vitrine sert à être trouvé. Modifier l'un ne doit pas changer ce que
    le monde voit de l'autre.
    """

    __tablename__ = "service_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )

    # ── Champs lus par le moteur de matching (noms alignés sur ScrapedOffer) ──
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Mots-clés libres saisis par le prestataire — pas de vocabulaire imposé.
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)

    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Tarif et disponibilité en texte libre : imposer une grille au démarrage
    # exclurait des métiers dont la tarification n'est ni horaire ni journalière.
    rate_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    availability_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Révélé au client uniquement après mise en relation (cf. MatchDecision).
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    status: Mapped[ProviderStatus] = mapped_column(
        Enum(ProviderStatus, name="provider_status_enum", create_constraint=False),
        nullable=False, default=ProviderStatus.draft, index=True,
    )
    # Consentement distinct : rendre sa vitrine publique expose des données
    # personnelles à des tiers, ce que le consentement d'inscription ne couvre pas.
    consent_public_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(EMBEDDING_DIM), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User")

    __table_args__ = (
        Index("ix_service_providers_status_country", "status", "country"),
    )


class ServiceRequest(Base):
    """Un besoin exprimé par un client."""

    __tablename__ = "service_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Fil de discussion associé : le besoin peut être affiné par l'IA, qui
    # enrichit la demande au fil de l'échange.
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_threads.id", ondelete="SET NULL"), nullable=True,
    )

    request_type: Mapped[RequestType] = mapped_column(
        Enum(RequestType, name="request_type_enum", create_constraint=False),
        nullable=False, default=RequestType.prestation,
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)

    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    budget_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, name="request_status_enum", create_constraint=False),
        nullable=False, default=RequestStatus.open, index=True,
    )
    # Renseigné quand le client choisit d'élargir au grand public. Le passage
    # n'est jamais automatique : c'est une décision explicite de sa part.
    published_public_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(EMBEDDING_DIM), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    requester = relationship("User")
    matches = relationship(
        "ServiceRequestMatch", back_populates="request", cascade="all, delete-orphan"
    )


class ServiceRequestMatch(Base):
    """Un couple (demande, destinataire) et l'état de sa double validation.

    `user_id` est toujours renseigné — c'est la personne notifiée.
    `provider_id` ne l'est que si elle possède une vitrine publiée ; il reste
    NULL pour les destinataires du grand public, dont le profil n'est révélé
    au client qu'après acceptation.
    """

    __tablename__ = "service_request_matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_requests.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_providers.id", ondelete="SET NULL"), nullable=True,
    )

    source: Mapped[MatchSource] = mapped_column(
        Enum(MatchSource, name="match_source_enum", create_constraint=False),
        nullable=False, default=MatchSource.provider,
    )
    decision: Mapped[MatchDecision] = mapped_column(
        Enum(MatchDecision, name="match_decision_enum", create_constraint=False),
        nullable=False, default=MatchDecision.pending, index=True,
    )

    # Conservés pour diagnostiquer a posteriori un rapprochement contesté —
    # même rôle que la trace de mode et de score côté offres.
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)

    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    provider_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    request = relationship("ServiceRequest", back_populates="matches")
    provider = relationship("ServiceProvider")
    user = relationship("User")

    __table_args__ = (
        # Une même personne n'est sollicitée qu'une fois par demande, quel que
        # soit le vivier — sinon un prestataire publié recevrait deux fois la
        # même demande lors du passage au grand public.
        UniqueConstraint("request_id", "user_id", name="uq_service_match_request_user"),
    )
