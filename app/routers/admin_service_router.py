"""Backoffice Services — supervision et modération de la mise en relation.

L'administrateur voit ce que ni le client ni le prestataire ne voient :
l'entonnoir complet, les identités, les scores de matching et le mode qui les
a produits. C'est ce qui permet de répondre à « pourquoi cette personne a-t-elle
reçu cette demande ? » — question qu'aucune des deux parties ne peut trancher
seule.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, case, func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_admin_user
from app.core.exceptions import NotFoundError
from app.models.service import (
    MatchDecision, ProviderStatus, RequestStatus, ServiceProvider,
    ServiceRequest, ServiceRequestMatch,
)
from app.models.user import Profile, User
from app.schemas.admin import AdminPaginated
from app.schemas.admin_service import (
    AdminMatchItem, AdminProviderDetail, AdminProviderItem, AdminProviderModerate,
    AdminRequestDetail, AdminRequestItem, AdminServiceStats, ServiceBucket, ServiceFunnel,
)

router = APIRouter(prefix="/admin/services", tags=["admin-services"])

_MONTH_ABBR_FR = [
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
]

_TYPE_LABELS = {
    "prestation": "Prestation", "emploi": "Emploi",
    "stage": "Stage", "autre": "Autre",
}


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _pag(total: int, page: int, size: int) -> dict:
    return {"total": total, "page": page, "size": size,
            "pages": max(1, (total + size - 1) // size)}


def _display_name(p: Profile | None) -> str | None:
    if not p or not p.first_name:
        return None
    return f"{p.first_name} {p.last_name or ''}".strip()


def _monthly(db: Session, date_col, months: int = 12) -> list[dict]:
    """Série mensuelle, mois vides inclus — une courbe à trous ment sur l'activité."""
    now = datetime.now(timezone.utc)
    total = (now.year * 12 + now.month - 1) - (months - 1)
    start = datetime(total // 12, total % 12 + 1, 1, tzinfo=timezone.utc)

    bucket = func.date_trunc("month", date_col).label("m")
    rows = db.execute(
        select(bucket, func.count()).where(date_col >= start).group_by(bucket)
    ).all()
    counts = {f"{m.year:04d}-{m.month:02d}": int(c) for m, c in rows if m is not None}

    series, cursor = [], start
    while cursor <= now:
        key = f"{cursor.year:04d}-{cursor.month:02d}"
        series.append({
            "period": key,
            "label": f"{_MONTH_ABBR_FR[cursor.month - 1]} {cursor.year % 100:02d}",
            "count": counts.get(key, 0),
        })
        cursor = (datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc) if cursor.month == 12
                  else datetime(cursor.year, cursor.month + 1, 1, tzinfo=timezone.utc))
    return series


def _buckets(rows: list[tuple], labels: dict[str, str] | None = None,
             limit: int = 10) -> list[ServiceBucket]:
    # Pourcentages calculés sur le total AVANT troncature : ils restent
    # cohérents même quand la queue de distribution n'est pas affichée.
    total = sum(int(c or 0) for _, c in rows) or 1
    out: list[ServiceBucket] = []
    for value, count in sorted(rows, key=lambda r: -(r[1] or 0))[:limit]:
        raw = value.value if hasattr(value, "value") else value
        key = str(raw).strip() if raw is not None and str(raw).strip() else "unknown"
        n = int(count or 0)
        out.append(ServiceBucket(
            key=key,
            label=(labels or {}).get(key, "Non renseigné" if key == "unknown" else key),
            count=n,
            pct=round(n / total * 100, 1),
        ))
    return out


# ── Statistiques ────────────────────────────────────────────────────────────


@router.get("/stats", response_model=AdminServiceStats)
def service_stats(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
):
    def count(model, *where):
        stmt = select(func.count(model.id))
        for w in where:
            stmt = stmt.where(w)
        return int(db.execute(stmt).scalar() or 0)

    providers_total = count(ServiceProvider)
    requests_total = count(ServiceRequest)
    matches_total = count(ServiceRequestMatch)

    responded = count(
        ServiceRequestMatch,
        ServiceRequestMatch.decision != MatchDecision.pending,
    )
    accepted = count(
        ServiceRequestMatch,
        ServiceRequestMatch.decision.in_([
            MatchDecision.provider_accepted, MatchDecision.client_accepted,
        ]),
    )
    connected = count(
        ServiceRequestMatch, ServiceRequestMatch.decision == MatchDecision.client_accepted
    )
    requests_with_matches = int(db.execute(
        select(func.count(func.distinct(ServiceRequestMatch.request_id)))
    ).scalar() or 0)

    avg_score = db.execute(
        select(func.avg(func.cast(ServiceRequestMatch.match_score, Float)))
    ).scalar()

    return AdminServiceStats(
        providers_total=providers_total,
        providers_published=count(ServiceProvider, ServiceProvider.status == ProviderStatus.published),
        providers_draft=count(ServiceProvider, ServiceProvider.status == ProviderStatus.draft),
        providers_suspended=count(ServiceProvider, ServiceProvider.status == ProviderStatus.suspended),
        requests_total=requests_total,
        requests_open=count(ServiceRequest, ServiceRequest.status == RequestStatus.open),
        requests_public=count(ServiceRequest, ServiceRequest.status == RequestStatus.public),
        requests_fulfilled=count(ServiceRequest, ServiceRequest.status == RequestStatus.fulfilled),
        funnel=ServiceFunnel(
            requests_total=requests_total,
            requests_with_matches=requests_with_matches,
            matches_total=matches_total,
            provider_accepted=accepted,
            connected=connected,
            provider_response_rate=_pct(responded, matches_total),
            acceptance_rate=_pct(accepted, matches_total),
            connection_rate=_pct(connected, accepted),
        ),
        requests_by_type=_buckets(
            db.execute(
                select(ServiceRequest.request_type, func.count()).group_by(ServiceRequest.request_type)
            ).all(),
            _TYPE_LABELS,
        ),
        providers_by_country=_buckets(
            db.execute(
                select(ServiceProvider.country, func.count()).group_by(ServiceProvider.country)
            ).all()
        ),
        requests_by_country=_buckets(
            db.execute(
                select(ServiceRequest.country, func.count()).group_by(ServiceRequest.country)
            ).all()
        ),
        monthly_requests=_monthly(db, ServiceRequest.created_at),
        monthly_providers=_monthly(db, ServiceProvider.created_at),
        avg_match_score=round(float(avg_score), 1) if avg_score is not None else None,
        # Une demande sans aucune sollicitation est le symptôme le plus grave :
        # le client n'a rien reçu et ne reviendra probablement pas.
        unmatched_requests=requests_total - requests_with_matches,
    )


# ── Prestataires ────────────────────────────────────────────────────────────


def _provider_counts(db: Session, provider_ids: list[uuid.UUID]) -> dict:
    if not provider_ids:
        return {}
    rows = db.execute(
        select(
            ServiceRequestMatch.provider_id,
            func.count().label("received"),
            func.sum(case((ServiceRequestMatch.decision.in_([
                MatchDecision.provider_accepted, MatchDecision.client_accepted,
            ]), 1), else_=0)).label("accepted"),
            func.sum(case((ServiceRequestMatch.decision == MatchDecision.client_accepted, 1),
                          else_=0)).label("connected"),
        )
        .where(ServiceRequestMatch.provider_id.in_(provider_ids))
        .group_by(ServiceRequestMatch.provider_id)
    ).all()
    return {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in rows}


@router.get("/providers", response_model=AdminPaginated[AdminProviderItem])
def list_providers(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Recherche sur le titre.")] = None,
):
    stmt = select(ServiceProvider, User, Profile).join(
        User, User.id == ServiceProvider.user_id
    ).outerjoin(Profile, Profile.user_id == ServiceProvider.user_id)
    count_stmt = select(func.count(ServiceProvider.id))

    if status:
        try:
            st = ProviderStatus(status)
            stmt = stmt.where(ServiceProvider.status == st)
            count_stmt = count_stmt.where(ServiceProvider.status == st)
        except ValueError:
            pass
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(ServiceProvider.title).like(like))
        count_stmt = count_stmt.where(func.lower(ServiceProvider.title).like(like))

    total = int(db.execute(count_stmt).scalar() or 0)
    rows = db.execute(
        stmt.order_by(ServiceProvider.created_at.desc())
        .offset((page - 1) * size).limit(size)
    ).all()

    counts = _provider_counts(db, [p.id for p, _, _ in rows])
    items = [
        AdminProviderItem(
            id=p.id, user_id=p.user_id, user_email=u.email, user_phone=u.phone,
            display_name=_display_name(prof), title=p.title,
            keywords=list(p.keywords or []), city=p.city, country=p.country,
            rate_text=p.rate_text, status=p.status,
            has_embedding=p.embedding is not None,
            received_count=counts.get(p.id, (0, 0, 0))[0],
            accepted_count=counts.get(p.id, (0, 0, 0))[1],
            connected_count=counts.get(p.id, (0, 0, 0))[2],
            published_at=p.published_at, created_at=p.created_at,
        )
        for p, u, prof in rows
    ]
    return AdminPaginated(items=items, **_pag(total, page, size))


@router.get("/providers/{provider_id}", response_model=AdminProviderDetail)
def get_provider(
    provider_id: uuid.UUID,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.execute(
        select(ServiceProvider, User, Profile)
        .join(User, User.id == ServiceProvider.user_id)
        .outerjoin(Profile, Profile.user_id == ServiceProvider.user_id)
        .where(ServiceProvider.id == provider_id)
    ).first()
    if not row:
        raise NotFoundError("Prestataire")
    p, u, prof = row
    c = _provider_counts(db, [p.id]).get(p.id, (0, 0, 0))

    return AdminProviderDetail(
        id=p.id, user_id=p.user_id, user_email=u.email, user_phone=u.phone,
        display_name=_display_name(prof), title=p.title, description=p.description,
        keywords=list(p.keywords or []), city=p.city, country=p.country,
        rate_text=p.rate_text, availability_text=p.availability_text,
        years_experience=p.years_experience, contact_phone=p.contact_phone,
        status=p.status, has_embedding=p.embedding is not None,
        received_count=c[0], accepted_count=c[1], connected_count=c[2],
        consent_public_at=p.consent_public_at,
        published_at=p.published_at, created_at=p.created_at,
    )


@router.patch("/providers/{provider_id}", response_model=AdminProviderItem)
def moderate_provider(
    provider_id: uuid.UUID,
    payload: AdminProviderModerate,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Modération d'une vitrine — suspension, réactivation.

    Suspendre la retire immédiatement du vivier de matching : le service
    n'interroge que les vitrines `published`.
    """
    p = db.get(ServiceProvider, provider_id)
    if not p:
        raise NotFoundError("Prestataire")
    p.status = payload.status
    db.commit()
    db.refresh(p)

    c = _provider_counts(db, [p.id]).get(p.id, (0, 0, 0))
    prof = db.execute(select(Profile).where(Profile.user_id == p.user_id)).scalar_one_or_none()
    u = db.get(User, p.user_id)
    return AdminProviderItem(
        id=p.id, user_id=p.user_id, user_email=u.email if u else None,
        user_phone=u.phone if u else None, display_name=_display_name(prof),
        title=p.title, keywords=list(p.keywords or []), city=p.city, country=p.country,
        rate_text=p.rate_text, status=p.status, has_embedding=p.embedding is not None,
        received_count=c[0], accepted_count=c[1], connected_count=c[2],
        published_at=p.published_at, created_at=p.created_at,
    )


# ── Demandes ────────────────────────────────────────────────────────────────


def _request_counts(db: Session, request_ids: list[uuid.UUID]) -> dict:
    if not request_ids:
        return {}
    rows = db.execute(
        select(
            ServiceRequestMatch.request_id,
            func.count().label("total"),
            func.sum(case((ServiceRequestMatch.decision.in_([
                MatchDecision.provider_accepted, MatchDecision.client_accepted,
            ]), 1), else_=0)).label("accepted"),
            func.sum(case((ServiceRequestMatch.decision == MatchDecision.client_accepted, 1),
                          else_=0)).label("connected"),
        )
        .where(ServiceRequestMatch.request_id.in_(request_ids))
        .group_by(ServiceRequestMatch.request_id)
    ).all()
    return {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in rows}


@router.get("/requests", response_model=AdminPaginated[AdminRequestItem])
def list_requests(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
    request_type: Annotated[str | None, Query()] = None,
    unmatched: Annotated[bool, Query(description="Seulement les demandes sans sollicitation.")] = False,
):
    stmt = select(ServiceRequest, Profile).outerjoin(
        Profile, Profile.user_id == ServiceRequest.requester_id
    )
    count_stmt = select(func.count(ServiceRequest.id))

    if status:
        try:
            st = RequestStatus(status)
            stmt = stmt.where(ServiceRequest.status == st)
            count_stmt = count_stmt.where(ServiceRequest.status == st)
        except ValueError:
            pass
    if request_type:
        stmt = stmt.where(ServiceRequest.request_type == request_type)
        count_stmt = count_stmt.where(ServiceRequest.request_type == request_type)
    if unmatched:
        sub = select(ServiceRequestMatch.request_id).distinct()
        stmt = stmt.where(ServiceRequest.id.not_in(sub))
        count_stmt = count_stmt.where(ServiceRequest.id.not_in(sub))

    total = int(db.execute(count_stmt).scalar() or 0)
    rows = db.execute(
        stmt.order_by(ServiceRequest.created_at.desc())
        .offset((page - 1) * size).limit(size)
    ).all()

    counts = _request_counts(db, [r.id for r, _ in rows])
    items = [
        AdminRequestItem(
            id=r.id, requester_id=r.requester_id, requester_name=_display_name(prof),
            request_type=r.request_type, title=r.title, city=r.city, country=r.country,
            budget_hint=r.budget_hint, status=r.status,
            has_embedding=r.embedding is not None,
            matches_count=counts.get(r.id, (0, 0, 0))[0],
            accepted_count=counts.get(r.id, (0, 0, 0))[1],
            connected_count=counts.get(r.id, (0, 0, 0))[2],
            published_public_at=r.published_public_at, created_at=r.created_at,
        )
        for r, prof in rows
    ]
    return AdminPaginated(items=items, **_pag(total, page, size))


@router.get("/requests/{request_id}", response_model=AdminRequestDetail)
def get_request(
    request_id: uuid.UUID,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Détail d'une demande avec TOUS ses rapprochements.

    C'est la vue qui permet de répondre à « pourquoi cette personne a-t-elle
    reçu cette demande ? » : score, mode de matching et vivier d'origine sont
    conservés pour chaque sollicitation.
    """
    row = db.execute(
        select(ServiceRequest, Profile)
        .outerjoin(Profile, Profile.user_id == ServiceRequest.requester_id)
        .where(ServiceRequest.id == request_id)
    ).first()
    if not row:
        raise NotFoundError("Demande")
    r, prof = row

    match_rows = db.execute(
        select(ServiceRequestMatch, Profile, ServiceProvider)
        .outerjoin(Profile, Profile.user_id == ServiceRequestMatch.user_id)
        .outerjoin(ServiceProvider, ServiceProvider.id == ServiceRequestMatch.provider_id)
        .where(ServiceRequestMatch.request_id == request_id)
        .order_by(ServiceRequestMatch.match_score.desc().nulls_last())
    ).all()

    matches = [
        AdminMatchItem(
            id=m.id, user_id=m.user_id, display_name=_display_name(mp),
            provider_title=sp.title if sp else None,
            source=m.source, decision=m.decision,
            match_score=m.match_score, match_mode=m.match_mode,
            notified_at=m.notified_at,
            provider_responded_at=m.provider_responded_at,
            client_responded_at=m.client_responded_at,
        )
        for m, mp, sp in match_rows
    ]
    c = _request_counts(db, [r.id]).get(r.id, (0, 0, 0))

    return AdminRequestDetail(
        id=r.id, requester_id=r.requester_id, requester_name=_display_name(prof),
        request_type=r.request_type, title=r.title, description=r.description,
        keywords=list(r.keywords or []), city=r.city, country=r.country,
        budget_hint=r.budget_hint, status=r.status,
        has_embedding=r.embedding is not None,
        matches_count=c[0], accepted_count=c[1], connected_count=c[2],
        published_public_at=r.published_public_at, created_at=r.created_at,
        matches=matches,
    )
