"""Routes de mise en relation prestataires ↔ clients.

Règle unique, appliquée par tous les endpoints de ce module : les coordonnées
ne circulent qu'après une double validation — le prestataire accepte la
demande, puis le client le retient. Cette contrainte est portée par la machine
à états de `MatchDecision`, jamais par des vérifications dispersées.

Chaque transition d'état vérifie l'état de départ. Un client ne peut pas
retenir quelqu'un qui n'a pas encore accepté, et un prestataire ne peut pas
revenir sur sa décision une fois la mise en relation faite.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.deps import get_current_user
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.notification import UserNotification
from app.models.service import (
    DeliveryMode, MatchDecision, MatchSource, ProviderStatus, RequestStatus,
    ServiceProvider, ServiceRequest, ServiceRequestMatch,
)
from app.models.user import Profile, User
from app.schemas.service import (
    DecisionRequest, MatchCardResponse, ProviderInboxItem, ProviderPublicCard,
    ProviderPublishRequest, ProviderResponse, ProviderUpsert,
    RequestCreate, RequestDetailResponse, RequestResponse, RequestUpdate,
)
from app.services.service_matching import ServiceMatchingService, _normalize_title

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/services", tags=["services"])


# ── Indexation sémantique, hors du chemin de requête ────────────────────────
#
# L'embedding passe par un appel HTTP externe (Perplexity) qui peut mettre
# plusieurs secondes, échouer, ou expirer. L'attendre avant de répondre rendait
# l'enregistrement d'une vitrine dépendant de la disponibilité d'un tiers :
# quota dépassé ou réseau lent, et l'utilisateur voyait « impossible
# d'enregistrer » alors que ses données étaient valides.
#
# On enregistre d'abord, on indexe ensuite. Une vitrine non encore indexée
# reste trouvable par la voie lexicale — la dégradation est invisible.


async def _index_provider_bg(provider_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        p = db.get(ServiceProvider, provider_id)
        if p is None:
            return
        await ServiceMatchingService(db).index_provider(p)
        db.commit()
    except Exception:
        logger.warning("[Services] indexation vitrine %s échouée", provider_id, exc_info=True)
        db.rollback()
    finally:
        db.close()


async def _index_and_rematch_bg(request_id: uuid.UUID) -> None:
    """Indexe la demande, puis complète le matching par la voie sémantique.

    Le premier tour lexical a déjà eu lieu et le client a déjà vu des profils :
    ce second passage ne fait qu'ajouter les prestataires que seuls les
    embeddings savent trouver.
    """
    db = SessionLocal()
    try:
        req = db.get(ServiceRequest, request_id)
        if req is None:
            return
        svc = ServiceMatchingService(db)
        await svc.index_request(req)
        db.commit()

        if req.embedding is None:
            return
        created = svc.create_matches(
            req, await svc.match_providers(req), MatchSource.provider
        )
        for m in created:
            _notify(db, m.user_id, f"Nouvelle demande : {req.title}")
        db.commit()
    except Exception:
        logger.warning("[Services] indexation demande %s échouée", request_id, exc_info=True)
        db.rollback()
    finally:
        db.close()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _display_name(profile: Profile | None) -> str:
    """Prénom + initiale du nom — jamais l'identité complète avant mise en relation."""
    if not profile or not profile.first_name:
        return "Prestataire"
    initial = f" {profile.last_name[0].upper()}." if profile.last_name else ""
    return f"{profile.first_name}{initial}"


def _provider_card(provider: ServiceProvider, profile: Profile | None) -> ProviderPublicCard:
    return ProviderPublicCard(
        provider_id=provider.id,
        display_name=_display_name(profile),
        title=provider.title,
        description=provider.description,
        keywords=list(provider.keywords or []),
        city=provider.city,
        country=provider.country,
        portfolio=provider.portfolio,
        rate_text=provider.rate_text,
        availability_text=provider.availability_text,
        years_experience=provider.years_experience,
    )


def _public_user_card(profile: Profile | None) -> ProviderPublicCard:
    """Carte d'un utilisateur du grand public, construite depuis son profil.

    Ces personnes n'ont pas de vitrine : on n'expose que ce qu'elles ont déjà
    renseigné, et uniquement après qu'elles aient accepté la demande.
    """
    return ProviderPublicCard(
        provider_id=None,
        display_name=_display_name(profile),
        title=(profile.domain if profile and profile.domain else "Profil disponible"),
        description=(profile.current_status or "") if profile else "",
        keywords=[],
        city=profile.city if profile else None,
        country=profile.country if profile else None,
    )


def _load_profiles(db: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, Profile]:
    if not user_ids:
        return {}
    rows = db.execute(select(Profile).where(Profile.user_id.in_(user_ids))).scalars().all()
    return {p.user_id: p for p in rows}


def _notify(db: Session, user_id: uuid.UUID, title: str) -> None:
    """Notification in-app — réutilise le canal existant des offres matchées."""
    db.add(UserNotification(user_id=user_id, offer_title=title[:500], seen=False))


def _get_own_request(db: Session, request_id: uuid.UUID, user: User) -> ServiceRequest:
    req = db.get(ServiceRequest, request_id)
    if not req:
        raise NotFoundError("Demande")
    if req.requester_id != user.id:
        raise ForbiddenError("Cette demande ne vous appartient pas.")
    return req


# ── Vitrine prestataire ─────────────────────────────────────────────────────


@router.get("/provider/me", response_model=ProviderResponse | None)
def get_my_provider(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Ma vitrine, ou null si je n'en ai pas encore créé."""
    return db.execute(
        select(ServiceProvider).where(ServiceProvider.user_id == current_user.id)
    ).scalar_one_or_none()


@router.put("/provider/me", response_model=ProviderResponse)
def upsert_my_provider(
    payload: ProviderUpsert,
    background: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Crée ou met à jour ma vitrine. Ne la publie pas — c'est un acte séparé."""
    provider = db.execute(
        select(ServiceProvider).where(ServiceProvider.user_id == current_user.id)
    ).scalar_one_or_none()

    if provider is None:
        provider = ServiceProvider(user_id=current_user.id, title=payload.title,
                                   description=payload.description)
        db.add(provider)

    provider.title = payload.title
    provider.description = payload.description
    provider.keywords = payload.keywords
    provider.city = payload.city
    provider.country = payload.country
    provider.portfolio = payload.portfolio
    provider.rate_text = payload.rate_text
    provider.availability_text = payload.availability_text
    provider.years_experience = payload.years_experience
    provider.contact_phone = payload.contact_phone or current_user.phone

    # Le titre normalisé est calculé ici — c'est du texte, pas un appel réseau,
    # et il conditionne la recherche lexicale qui doit marcher immédiatement.
    provider.normalized_title = _normalize_title(provider.title) or None

    db.commit()
    db.refresh(provider)

    # L'embedding part en tâche de fond : la réponse ne l'attend pas.
    background.add_task(_index_provider_bg, provider.id)
    return provider


@router.post("/provider/me/publish", response_model=ProviderResponse)
def publish_my_provider(
    payload: ProviderPublishRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Rend ma vitrine visible. Exige un consentement explicite.

    Publier expose des données personnelles à des tiers, ce que le consentement
    donné à l'inscription ne couvre pas (Loi CI 2013-450, art. 6).
    """
    provider = db.execute(
        select(ServiceProvider).where(ServiceProvider.user_id == current_user.id)
    ).scalar_one_or_none()
    if not provider:
        raise NotFoundError("Vitrine")
    if not payload.consent_public:
        raise BadRequestError(
            "La publication requiert votre accord explicite pour rendre votre "
            "profil visible et le proposer à des clients."
        )

    provider.status = ProviderStatus.published
    provider.consent_public_at = datetime.now(timezone.utc)
    provider.published_at = provider.published_at or datetime.now(timezone.utc)
    db.commit()
    db.refresh(provider)
    return provider


@router.post("/provider/me/unpublish", response_model=ProviderResponse)
def unpublish_my_provider(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Retire ma vitrine du vivier. Réversible à tout moment."""
    provider = db.execute(
        select(ServiceProvider).where(ServiceProvider.user_id == current_user.id)
    ).scalar_one_or_none()
    if not provider:
        raise NotFoundError("Vitrine")
    provider.status = ProviderStatus.paused
    db.commit()
    db.refresh(provider)
    return provider


# ── Demandes — côté client ──────────────────────────────────────────────────


@router.post("/requests", response_model=RequestDetailResponse, status_code=201)
def create_request(
    payload: RequestCreate,
    background: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Publie une demande et sollicite immédiatement les prestataires publiés.

    Le grand public n'est jamais contacté à ce stade : c'est une décision que
    le client prend séparément, s'il ne trouve pas son bonheur.
    """
    req = ServiceRequest(
        requester_id=current_user.id,
        request_type=payload.request_type,
        title=payload.title,
        description=payload.description,
        keywords=payload.keywords,
        delivery_mode=payload.delivery_mode,
        city=payload.city,
        country=payload.country,
        budget_hint=payload.budget_hint,
        contact_phone=payload.contact_phone,
        status=RequestStatus.open,
    )
    db.add(req)
    db.flush()

    # Matching lexical immédiat : il ne dépend d'aucun service externe, et le
    # client doit voir des profils tout de suite — une page vide le ferait
    # partir. La voie sémantique complètera en tâche de fond.
    req.normalized_title = _normalize_title(req.title) or None
    svc = ServiceMatchingService(db)
    created = svc.create_matches(
        req, svc.match_providers_lexical(req), MatchSource.provider
    )
    for m in created:
        _notify(db, m.user_id, f"Nouvelle demande : {req.title}")

    db.commit()
    db.refresh(req)

    background.add_task(_index_and_rematch_bg, req.id)
    return _build_detail(db, req)


@router.get("/requests", response_model=list[RequestResponse])
def list_my_requests(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return list(db.execute(
        select(ServiceRequest)
        .where(ServiceRequest.requester_id == current_user.id)
        .order_by(ServiceRequest.created_at.desc())
    ).scalars().all())


@router.get("/requests/{request_id}", response_model=RequestDetailResponse)
def get_request(
    request_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return _build_detail(db, _get_own_request(db, request_id, current_user))


@router.patch("/requests/{request_id}", response_model=RequestDetailResponse)
def update_request(
    request_id: uuid.UUID,
    payload: RequestUpdate,
    background: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Modifie une demande déjà publiée.

    Interdit une fois qu'un prestataire a été retenu ou que le client a
    clôturé : à ce stade, changer le contenu tromperait la personne déjà
    engagée sur une base différente. Reste ouvert tant que la demande
    cherche encore — y compris après élargissement au grand public.

    Ne notifie jamais les prestataires déjà sollicités : seule une nouvelle
    passe de matching en tâche de fond peut en trouver de nouveaux, les
    autres ne sont pas dérangés pour un changement qu'ils ne verront jamais.
    """
    req = _get_own_request(db, request_id, current_user)
    if req.status not in (RequestStatus.open, RequestStatus.public):
        raise BadRequestError(
            "Cette demande ne peut plus être modifiée — un prestataire a déjà "
            "été retenu, ou elle est clôturée."
        )

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(req, field, value)

    # À distance, la ville et le pays ne veulent plus rien dire — même règle
    # qu'à la création, qu'ils aient été fournis dans cette mise à jour ou
    # qu'ils traînent d'un état antérieur de la demande.
    if req.delivery_mode == DeliveryMode.remote:
        req.city = None
        req.country = None

    if "title" in data:
        req.normalized_title = _normalize_title(req.title) or None

    db.commit()
    db.refresh(req)

    # Le contenu a changé : ré-indexer pour que la recherche sémantique et un
    # éventuel élargissement au grand public reflètent la nouvelle version.
    background.add_task(_index_and_rematch_bg, req.id)
    return _build_detail(db, req)


@router.post("/requests/{request_id}/go-public", response_model=RequestDetailResponse)
def go_public(
    request_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Élargit la demande au grand public, à la main du client.

    Ce n'est jamais automatique : le client regarde d'abord les prestataires
    proposés, et décide lui-même d'aller plus loin. Les personnes contactées
    ici n'ont pas de vitrine publique — leur profil ne sera montré qu'après
    qu'elles aient accepté.
    """
    req = _get_own_request(db, request_id, current_user)
    if req.status not in (RequestStatus.open, RequestStatus.public):
        raise BadRequestError("Cette demande n'est plus ouverte.")

    svc = ServiceMatchingService(db)
    created = svc.create_matches(req, svc.match_public(req), MatchSource.public)
    for m in created:
        _notify(db, m.user_id, f"Une demande correspond à votre objectif : {req.title}")

    req.status = RequestStatus.public
    req.published_public_at = req.published_public_at or datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)
    return _build_detail(db, req)


@router.post("/requests/{request_id}/matches/{match_id}/decide",
             response_model=RequestDetailResponse)
def client_decide(
    request_id: uuid.UUID,
    match_id: uuid.UUID,
    payload: DecisionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Le client retient — ou écarte — un prestataire qui a accepté.

    Second volet de la double validation : c'est cette action qui débloque
    l'échange des coordonnées, dans les deux sens.
    """
    req = _get_own_request(db, request_id, current_user)
    match = db.get(ServiceRequestMatch, match_id)
    if not match or match.request_id != req.id:
        raise NotFoundError("Rapprochement")
    if match.decision != MatchDecision.provider_accepted:
        raise BadRequestError(
            "Vous ne pouvez retenir qu'un prestataire ayant déjà accepté votre demande."
        )

    match.decision = (
        MatchDecision.client_accepted if payload.accept else MatchDecision.client_declined
    )
    match.client_responded_at = datetime.now(timezone.utc)

    if payload.accept:
        req.status = RequestStatus.fulfilled
        _notify(db, match.user_id, f"Vous avez été retenu : {req.title}")

    db.commit()
    db.refresh(req)
    return _build_detail(db, req)


@router.post("/requests/{request_id}/close", response_model=RequestResponse)
def close_request(
    request_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    req = _get_own_request(db, request_id, current_user)
    req.status = RequestStatus.closed
    db.commit()
    db.refresh(req)
    return req


# ── Boîte de réception — côté prestataire ───────────────────────────────────


@router.get("/inbox", response_model=list[ProviderInboxItem])
def my_inbox(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    only_pending: Annotated[bool, Query(description="N'afficher que les demandes sans réponse.")] = False,
):
    """Les demandes qui m'ont été adressées, quel que soit le vivier."""
    stmt = (
        select(ServiceRequestMatch, ServiceRequest)
        .join(ServiceRequest, ServiceRequest.id == ServiceRequestMatch.request_id)
        .where(ServiceRequestMatch.user_id == current_user.id)
        .order_by(ServiceRequestMatch.notified_at.desc())
    )
    if only_pending:
        stmt = stmt.where(ServiceRequestMatch.decision == MatchDecision.pending)

    rows = db.execute(stmt).all()
    requester_ids = [r.requester_id for _, r in rows]
    profiles = _load_profiles(db, requester_ids)
    phones = {
        u.id: u.phone for u in db.execute(
            select(User).where(User.id.in_(requester_ids))
        ).scalars().all()
    } if requester_ids else {}

    items: list[ProviderInboxItem] = []
    for match, req in rows:
        # L'identité du client n'est révélée qu'une fois la mise en relation
        # établie — symétrique de ce que voit le client.
        connected = match.decision == MatchDecision.client_accepted
        items.append(ProviderInboxItem(
            match_id=match.id,
            request_id=req.id,
            request_type=req.request_type,
            title=req.title,
            description=req.description,
            delivery_mode=req.delivery_mode,
            city=req.city,
            country=req.country,
            budget_hint=req.budget_hint,
            match_score=match.match_score,
            decision=match.decision,
            notified_at=match.notified_at,
            client_display_name=(
                _display_name(profiles.get(req.requester_id)) if connected else None
            ),
            # Le numéro donné pour CETTE demande prime — c'est celui que le
            # client a choisi de communiquer ; le numéro de compte ne sert
            # qu'aux demandes créées avant l'existence de ce champ.
            client_phone=(req.contact_phone or phones.get(req.requester_id)) if connected else None,
        ))
    return items


@router.post("/inbox/{match_id}/decide", response_model=ProviderInboxItem)
def provider_decide(
    match_id: uuid.UUID,
    payload: DecisionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """J'accepte — ou je décline — une demande reçue.

    Premier volet de la double validation. Accepter ne donne accès à rien :
    cela rend seulement mon profil visible du client, qui décidera ensuite.
    """
    match = db.get(ServiceRequestMatch, match_id)
    if not match:
        raise NotFoundError("Demande")
    if match.user_id != current_user.id:
        raise ForbiddenError("Cette demande ne vous est pas adressée.")
    if match.decision != MatchDecision.pending:
        raise BadRequestError("Vous avez déjà répondu à cette demande.")

    match.decision = (
        MatchDecision.provider_accepted if payload.accept else MatchDecision.provider_declined
    )
    match.provider_responded_at = datetime.now(timezone.utc)

    req = db.get(ServiceRequest, match.request_id)
    if payload.accept and req:
        _notify(db, req.requester_id, f"Un prestataire a accepté : {req.title}")

    db.commit()

    profile = db.execute(
        select(Profile).where(Profile.user_id == req.requester_id)
    ).scalar_one_or_none() if req else None
    connected = match.decision == MatchDecision.client_accepted

    return ProviderInboxItem(
        match_id=match.id,
        request_id=match.request_id,
        request_type=req.request_type if req else None,
        title=req.title if req else "",
        description=req.description if req else "",
        city=req.city if req else None,
        country=req.country if req else None,
        budget_hint=req.budget_hint if req else None,
        match_score=match.match_score,
        decision=match.decision,
        notified_at=match.notified_at,
        client_display_name=_display_name(profile) if connected else None,
        client_phone=None,
    )


# ── Assemblage de la vue client ─────────────────────────────────────────────


def _build_detail(db: Session, req: ServiceRequest) -> RequestDetailResponse:
    """Regroupe les rapprochements par état, avec le bon niveau de détail.

    Le tri est fait ici plutôt que côté interface pour que la règle de
    visibilité — qui voit quoi, et quand — n'existe qu'à un seul endroit.
    """
    matches = list(db.execute(
        select(ServiceRequestMatch)
        .where(ServiceRequestMatch.request_id == req.id)
        .order_by(ServiceRequestMatch.match_score.desc().nulls_last())
    ).scalars().all())

    provider_ids = [m.provider_id for m in matches if m.provider_id]
    providers = {
        p.id: p for p in (
            db.execute(
                select(ServiceProvider).where(ServiceProvider.id.in_(provider_ids))
            ).scalars().all() if provider_ids else []
        )
    }
    profiles = _load_profiles(db, [m.user_id for m in matches])
    phones = {
        u.id: u.phone for u in db.execute(
            select(User).where(User.id.in_([m.user_id for m in matches]))
        ).scalars().all()
    } if matches else {}

    accepted: list[MatchCardResponse] = []
    pending: list[MatchCardResponse] = []
    connected: list[MatchCardResponse] = []
    declined = 0

    for m in matches:
        if m.decision in (MatchDecision.provider_declined, MatchDecision.client_declined,
                          MatchDecision.expired):
            declined += 1
            continue

        provider = providers.get(m.provider_id) if m.provider_id else None
        is_connected = m.decision == MatchDecision.client_accepted

        # Une vitrine publiée est visible dès la sollicitation : son propriétaire
        # a consenti à l'être. Un utilisateur du grand public ne l'est qu'après
        # avoir accepté — il n'a jamais demandé à être exposé.
        show_card = provider is not None or m.decision in (
            MatchDecision.provider_accepted, MatchDecision.client_accepted,
        )
        card = None
        if show_card:
            card = (
                _provider_card(provider, profiles.get(m.user_id)) if provider
                else _public_user_card(profiles.get(m.user_id))
            )

        item = MatchCardResponse(
            id=m.id,
            decision=m.decision,
            source=m.source,
            match_score=m.match_score,
            notified_at=m.notified_at,
            card=card,
            # Le téléphone n'apparaît qu'après double validation.
            contact_phone=(
                (provider.contact_phone if provider else None) or phones.get(m.user_id)
            ) if is_connected else None,
        )

        if is_connected:
            connected.append(item)
        elif m.decision == MatchDecision.provider_accepted:
            accepted.append(item)
        else:
            pending.append(item)

    return RequestDetailResponse(
        id=req.id,
        request_type=req.request_type,
        title=req.title,
        description=req.description,
        keywords=list(req.keywords or []),
        city=req.city,
        country=req.country,
        budget_hint=req.budget_hint,
        status=req.status,
        published_public_at=req.published_public_at,
        created_at=req.created_at,
        accepted=accepted,
        pending=pending,
        connected=connected,
        declined_count=declined,
        can_go_public=req.status == RequestStatus.open,
    )
