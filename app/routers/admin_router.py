"""Backoffice admin — endpoints complets."""
from __future__ import annotations
import math
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, desc
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deps import get_admin_user
from app.models.chat import ChatMessage, ChatThread, ThreadStatus
from app.models.document import Document
from app.models.goal import Goal, GoalStatus, GoalType
from app.models.scraped_offer import ScrapedOffer
from app.models.user import Profile, User, UserRole
from app.models.user_intent import UserIntent
from app.models.structure import (
    Classroom, ClassroomCourse, ClassroomMembership, ClassroomTeacher,
    InvitationStatus, MembershipStatus, Structure, StructureInvitation,
    StructureMember, StructureMemberRole, StructureStatus,
)
from app.schemas.admin import (AdminDeliverableItem,
    AdminOfferCreate, AdminUserCreate, AdminDocumentItem, AdminGoalItem, AdminIntentItem, AdminMessageItem, AdminOfferDetail, AdminOfferItem, AdminOfferUpdate, AdminPaginated, AdminStats, AdminStructureClassroom, AdminStructureDetail, AdminStructureInvitation, AdminStructureItem, AdminStructureMember, AdminStructureUpdate, AdminThreadDetail, AdminThreadItem, AdminUserDetail, AdminUserItem, AdminUserUpdate)
from app.services import structure_service

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

def _pag(total, page, size): return {"total": total, "page": page, "size": size}

def _user_item(u, p, tc, gc, dc):
    return AdminUserItem(
        id=str(u.id), email=u.email, phone=u.phone,
        role=getattr(u.role, "value", u.role) if u.role else "b2c",
        is_active=u.is_active, created_at=u.created_at,
        first_name=p.first_name if p else None,
        last_name=p.last_name if p else None,
        primary_role=getattr(p.primary_role, "value", p.primary_role) if p and p.primary_role else None,
        country=p.country if p else None,
        threads_count=tc or 0, goals_count=gc or 0, documents_count=dc or 0,
    )

def _ufilter(q_obj, q, role, active):
    if q:
        q_obj = q_obj.filter(or_(User.email.ilike(f"%{q}%"), User.phone.ilike(f"%{q}%"), Profile.first_name.ilike(f"%{q}%"), Profile.last_name.ilike(f"%{q}%")))
    if role:
        try: q_obj = q_obj.filter(User.role == UserRole(role))
        except ValueError: pass
    if active is not None: q_obj = q_obj.filter(User.is_active == active)
    return q_obj

@router.get("/stats", response_model=AdminStats)
def get_stats(admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    now = datetime.now(timezone.utc)
    return AdminStats(
        users_total=db.query(func.count(User.id)).scalar() or 0,
        users_new_7d=db.query(func.count(User.id)).filter(User.created_at >= now - timedelta(days=7)).scalar() or 0,
        threads_total=db.query(func.count(ChatThread.id)).scalar() or 0,
        messages_today=db.query(func.count(ChatMessage.id)).filter(ChatMessage.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)).scalar() or 0,
        offers_total=db.query(func.count(ScrapedOffer.id)).scalar() or 0,
        offers_active=db.query(func.count(ScrapedOffer.id)).filter(ScrapedOffer.is_active.is_(True)).scalar() or 0,
        documents_total=db.query(func.count(Document.id)).scalar() or 0,
        intents_total=db.query(func.count(UserIntent.id)).scalar() or 0,
        goals_total=db.query(func.count(Goal.id)).scalar() or 0,
    )

@router.get("/users", response_model=AdminPaginated[AdminUserItem])
def list_users(admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)], page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), q: str | None = None, role: str | None = None, active: bool | None = None):
    tsq = select(func.count(ChatThread.id)).where(ChatThread.user_id == User.id).correlate(User).scalar_subquery()
    gsq = select(func.count(Goal.id)).where(Goal.user_id == User.id).correlate(User).scalar_subquery()
    dsq = select(func.count(Document.id)).where(Document.user_id == User.id).correlate(User).scalar_subquery()
    total = _ufilter(db.query(func.count(User.id)).outerjoin(Profile, Profile.user_id == User.id), q, role, active).scalar() or 0
    rows = _ufilter(db.query(User, Profile, tsq, gsq, dsq).outerjoin(Profile, Profile.user_id == User.id), q, role, active).order_by(desc(User.created_at)).offset((page-1)*size).limit(size).all()
    return AdminPaginated(items=[_user_item(u, p, tc, gc, dc) for u, p, tc, gc, dc in rows], **_pag(total, page, size))

@router.get("/users/{user_id}", response_model=AdminUserDetail)
def get_user(user_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    u = db.get(User, user_id)
    if not u: raise HTTPException(404, "Utilisateur introuvable.")
    p = u.profile
    tc = db.query(func.count(ChatThread.id)).filter(ChatThread.user_id == user_id).scalar() or 0
    gc = db.query(func.count(Goal.id)).filter(Goal.user_id == user_id).scalar() or 0
    dc = db.query(func.count(Document.id)).filter(Document.user_id == user_id).scalar() or 0
    rthreads = [AdminThreadItem(id=str(t.id), user_id=str(t.user_id), user_email=u.email, title=t.title, status=t.status.value, message_count=t.message_count, created_at=t.created_at) for t in db.query(ChatThread).filter(ChatThread.user_id == user_id).order_by(desc(ChatThread.created_at)).limit(5).all()]
    rgoals = [AdminGoalItem(id=str(g.id), user_id=str(g.user_id), user_email=u.email, type=g.type.value, status=g.status.value, preset_key=(g.context_data or {}).get("preset_key"), created_at=g.created_at, threads_count=db.query(func.count(ChatThread.id)).filter(ChatThread.goal_id == g.id).scalar() or 0) for g in db.query(Goal).filter(Goal.user_id == user_id).order_by(desc(Goal.created_at)).limit(5).all()]
    return AdminUserDetail(
        id=str(u.id), email=u.email, phone=u.phone,
        role=getattr(u.role, "value", u.role) if u.role else "b2c",
        is_active=u.is_active, created_at=u.created_at,
        first_name=p.first_name if p else None, last_name=p.last_name if p else None,
        primary_role=getattr(p.primary_role, "value", p.primary_role) if p and p.primary_role else None,
        country=p.country if p else None,
        threads_count=tc, goals_count=gc, documents_count=dc,
        domain=p.domain if p else None, field_of_study=p.field_of_study if p else None,
        city=p.city if p else None, birth_year=p.birth_year if p else None,
        gender=getattr(p.gender, "value", p.gender) if p and p.gender else None,
        language=p.language if p else None,
        recent_threads=rthreads, recent_goals=rgoals,
    )

@router.patch("/users/{user_id}", response_model=AdminUserItem)
def update_user(user_id: UUID, payload: AdminUserUpdate, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    u = db.get(User, user_id)
    if not u: raise HTTPException(404, "Utilisateur introuvable.")
    if str(u.id) == str(admin.id): raise HTTPException(400, "Impossible de modifier son propre compte.")
    if payload.role is not None:
        try: u.role = UserRole(payload.role)
        except ValueError: raise HTTPException(400, "Role invalide.")
    if payload.is_active is not None: u.is_active = payload.is_active
    db.commit(); db.refresh(u)
    p = u.profile
    tc = db.query(func.count(ChatThread.id)).filter(ChatThread.user_id == user_id).scalar() or 0
    gc = db.query(func.count(Goal.id)).filter(Goal.user_id == user_id).scalar() or 0
    dc = db.query(func.count(Document.id)).filter(Document.user_id == user_id).scalar() or 0
    return _user_item(u, p, tc, gc, dc)

@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    u = db.get(User, user_id)
    if not u: raise HTTPException(404, "Utilisateur introuvable.")
    if str(u.id) == str(admin.id): raise HTTPException(400, "Impossible de supprimer son propre compte.")
    db.delete(u); db.commit()

@router.get("/offers", response_model=AdminPaginated[AdminOfferItem])
def list_offers(admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)], page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), offer_type: str | None = None, source: str | None = None, source_prefix: str | None = None, active: bool | None = None, q: str | None = None):
    base = db.query(ScrapedOffer)
    if offer_type: base = base.filter(ScrapedOffer.offer_type == offer_type)
    if source: base = base.filter(ScrapedOffer.source == source)
    if source_prefix: base = base.filter(ScrapedOffer.source.ilike(f"{source_prefix}%"))
    if active is not None: base = base.filter(ScrapedOffer.is_active.is_(active))
    if q: base = base.filter(or_(ScrapedOffer.title.ilike(f"%{q}%"), ScrapedOffer.company.ilike(f"%{q}%")))
    total = base.with_entities(func.count(ScrapedOffer.id)).scalar() or 0
    rows = base.order_by(desc(ScrapedOffer.scraped_at)).offset((page-1)*size).limit(size).all()
    items = [AdminOfferItem(id=str(o.id), source=o.source, offer_type=o.offer_type.value if o.offer_type else None, title=o.title, company=o.company, location=o.location, url=o.url, quality_score=o.quality_score, is_active=o.is_active, has_embedding=o.embedding is not None, scraped_at=o.scraped_at, posted_at=o.posted_at, expires_at=o.expires_at) for o in rows]
    return AdminPaginated(items=items, **_pag(total, page, size))

@router.get("/offers/{offer_id}", response_model=AdminOfferDetail)
def get_offer(offer_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    """Détail complet d'une offre — inclut la description pour la curation manuelle."""
    o = db.get(ScrapedOffer, offer_id)
    if not o: raise HTTPException(404, "Offre introuvable.")
    return AdminOfferDetail(
        id=str(o.id), source=o.source,
        offer_type=o.offer_type.value if o.offer_type else None,
        title=o.title, company=o.company, location=o.location,
        url=o.url, quality_score=o.quality_score, is_active=o.is_active,
        has_embedding=o.embedding is not None,
        scraped_at=o.scraped_at, posted_at=o.posted_at, expires_at=o.expires_at,
        description=o.description, external_id=o.external_id or str(o.id),
        normalized_title=o.normalized_title if hasattr(o, "normalized_title") else None,
        salary=o.salary,
    )

@router.patch("/offers/{offer_id}", response_model=AdminOfferItem)
def update_offer(offer_id: UUID, payload: AdminOfferUpdate, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    from app.models.scraped_offer import ScrapedOfferType as SOT
    o = db.get(ScrapedOffer, offer_id)
    if not o: raise HTTPException(404, "Offre introuvable.")
    if payload.is_active is not None: o.is_active = payload.is_active
    if payload.quality_score is not None: o.quality_score = payload.quality_score
    if payload.title is not None: o.title = payload.title
    if payload.offer_type is not None:
        try: o.offer_type = SOT(payload.offer_type)
        except ValueError: pass
    if payload.description is not None: o.description = payload.description
    if payload.url is not None: o.url = payload.url
    if payload.company is not None: o.company = payload.company
    if payload.location is not None: o.location = payload.location
    if payload.salary is not None: o.salary = payload.salary
    if payload.posted_at is not None: o.posted_at = payload.posted_at
    if payload.expires_at is not None: o.expires_at = payload.expires_at
    # Si la description change, invalider l'embedding (il sera regenere par le scheduler)
    if payload.description is not None or payload.title is not None:
        o.embedding = None
    db.commit(); db.refresh(o)
    logger.info("admin_offer_updated", offer_id=str(o.id), by=str(admin.id))
    return AdminOfferItem(id=str(o.id), source=o.source, offer_type=o.offer_type.value if o.offer_type else None, title=o.title, company=o.company, location=o.location, url=o.url, quality_score=o.quality_score, is_active=o.is_active, has_embedding=o.embedding is not None, scraped_at=o.scraped_at, posted_at=o.posted_at, expires_at=o.expires_at)

@router.delete("/offers/{offer_id}", status_code=204)
def delete_offer(offer_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    o = db.get(ScrapedOffer, offer_id)
    if not o: raise HTTPException(404, "Offre introuvable.")
    db.delete(o); db.commit()

@router.get("/goals", response_model=AdminPaginated[AdminGoalItem])
def list_goals(admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)], page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), goal_type: str | None = None, status: str | None = None, user_id: UUID | None = None):
    tsq = select(func.count(ChatThread.id)).where(ChatThread.goal_id == Goal.id).correlate(Goal).scalar_subquery()
    base = db.query(Goal, User, tsq).join(User, User.id == Goal.user_id)
    if goal_type:
        try: base = base.filter(Goal.type == GoalType(goal_type))
        except ValueError: pass
    if status:
        try: base = base.filter(Goal.status == GoalStatus(status))
        except ValueError: pass
    if user_id: base = base.filter(Goal.user_id == user_id)
    cq = db.query(func.count(Goal.id)).join(User, User.id == Goal.user_id)
    if goal_type:
        try: cq = cq.filter(Goal.type == GoalType(goal_type))
        except ValueError: pass
    if status:
        try: cq = cq.filter(Goal.status == GoalStatus(status))
        except ValueError: pass
    if user_id: cq = cq.filter(Goal.user_id == user_id)
    total = cq.scalar() or 0
    rows = base.order_by(desc(Goal.created_at)).offset((page-1)*size).limit(size).all()
    items = [AdminGoalItem(id=str(g.id), user_id=str(g.user_id), user_email=u.email, type=g.type.value, status=g.status.value, preset_key=(g.context_data or {}).get("preset_key"), created_at=g.created_at, threads_count=tc or 0) for g, u, tc in rows]
    return AdminPaginated(items=items, **_pag(total, page, size))

@router.get("/threads", response_model=AdminPaginated[AdminThreadItem])
def list_threads(admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)], page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), status: str | None = None, user_id: UUID | None = None, q: str | None = None):
    base = db.query(ChatThread, User).join(User, User.id == ChatThread.user_id)
    cq = db.query(func.count(ChatThread.id)).join(User, User.id == ChatThread.user_id)
    if status:
        try: f = ChatThread.status == ThreadStatus(status); base = base.filter(f); cq = cq.filter(f)
        except ValueError: pass
    if user_id: base = base.filter(ChatThread.user_id == user_id); cq = cq.filter(ChatThread.user_id == user_id)
    if q: f = ChatThread.title.ilike(f"%{q}%"); base = base.filter(f); cq = cq.filter(f)
    total = cq.scalar() or 0
    rows = base.order_by(desc(ChatThread.created_at)).offset((page-1)*size).limit(size).all()
    items = [AdminThreadItem(id=str(t.id), user_id=str(t.user_id), user_email=u.email, title=t.title, status=t.status.value, message_count=t.message_count, created_at=t.created_at) for t, u in rows]
    return AdminPaginated(items=items, **_pag(total, page, size))

@router.get("/threads/{thread_id}", response_model=AdminThreadDetail)
def get_thread(thread_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    t = db.get(ChatThread, thread_id)
    if not t: raise HTTPException(404, "Thread introuvable.")
    u = db.get(User, t.user_id)
    msgs = db.query(ChatMessage).filter(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at).all()
    return AdminThreadDetail(id=str(t.id), user_id=str(t.user_id), user_email=u.email if u else None, title=t.title, status=t.status.value, message_count=t.message_count, created_at=t.created_at, messages=[AdminMessageItem(id=str(m.id), role=m.role.value, content=m.content, created_at=m.created_at, agent_id=m.agent_id, processing_ms=m.processing_ms, is_active=m.is_active) for m in msgs])

@router.delete("/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    t = db.get(ChatThread, thread_id)
    if not t: raise HTTPException(404, "Thread introuvable.")
    db.delete(t); db.commit()

@router.get("/documents", response_model=AdminPaginated[AdminDocumentItem])
def list_documents(admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)], page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), doc_type: str | None = None, user_id: UUID | None = None):
    base = db.query(Document, User).join(User, User.id == Document.user_id)
    cq = db.query(func.count(Document.id)).join(User, User.id == Document.user_id)
    if doc_type: base = base.filter(Document.type == doc_type); cq = cq.filter(Document.type == doc_type)
    if user_id: base = base.filter(Document.user_id == user_id); cq = cq.filter(Document.user_id == user_id)
    total = cq.scalar() or 0
    rows = base.order_by(desc(Document.created_at)).offset((page-1)*size).limit(size).all()
    items = [AdminDocumentItem(id=str(d.id), user_id=str(d.user_id), user_email=u.email, type=d.type.value, version=d.version, content_preview=d.content[:200] if d.content else "", created_at=d.created_at) for d, u in rows]
    return AdminPaginated(items=items, **_pag(total, page, size))

@router.get("/intents", response_model=AdminPaginated[AdminIntentItem])
def list_intents(admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)], page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), intent_type: str | None = None, user_id: UUID | None = None):
    base = db.query(UserIntent, User).join(User, User.id == UserIntent.user_id)
    cq = db.query(func.count(UserIntent.id)).join(User, User.id == UserIntent.user_id)
    if intent_type: base = base.filter(UserIntent.intent_type == intent_type); cq = cq.filter(UserIntent.intent_type == intent_type)
    if user_id: base = base.filter(UserIntent.user_id == user_id); cq = cq.filter(UserIntent.user_id == user_id)
    total = cq.scalar() or 0
    rows = base.order_by(desc(UserIntent.extracted_at)).offset((page-1)*size).limit(size).all()
    items = [AdminIntentItem(id=str(i.id), user_id=str(i.user_id), user_email=u.email, intent_type=i.intent_type, intent_summary=i.intent_summary, domain=i.domain, location=i.location, level=i.level, keywords=i.keywords, version=i.version, extracted_at=i.extracted_at) for i, u in rows]
    return AdminPaginated(items=items, **_pag(total, page, size))


# ── Création d'offre manuelle ──────────────────────────────────────────────

@router.post("/offers", response_model=AdminOfferItem, status_code=201)
def create_offer(
    payload: AdminOfferCreate,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminOfferItem:
    """Crée une offre manuellement (WhatsApp, Telegram, etc.).
    L'embedding est généré automatiquement par le prochain run du scheduler.
    L'offre est immédiatement éligible au matching.
    """
    import uuid as _uuid
    from app.models.scraped_offer import ScrapedOfferType
    from app.services.scraping.pipeline import process_offer

    # Résoudre l'offer_type vers l'enum
    try:
        ot = ScrapedOfferType(payload.offer_type)
    except ValueError:
        ot = ScrapedOfferType.opportunity

    offer = ScrapedOffer(
        source=payload.source or "admin",
        external_id=str(_uuid.uuid4()),
        offer_type=ot,
        title=payload.title,
        description=payload.description,
        url=payload.url,
        company=payload.company,
        location=payload.location,
        salary=payload.salary,
        posted_at=payload.posted_at,
        expires_at=payload.expires_at,
        scraped_at=datetime.now(timezone.utc),
        is_active=True,
        quality_score=None,
        embedding=None,  # généré par le prochain run scheduler
    )
    db.add(offer)
    db.flush()  # attribue offer.id, nécessaire pour process_offer

    # Même pipeline que les scrapers (normalized_title + quality_score) :
    # sans cet appel, quality_score restait NULL et la recherche par
    # mots-clés (ORDER BY quality_score DESC NULLS LAST) exclut ces offres
    # du lot examiné dès que le nombre de candidats concurrents dépasse la
    # limite de la requête — invisibles au matching sans jamais d'erreur.
    process_offer(db, offer.id)

    db.commit()
    db.refresh(offer)

    logger.info("admin_offer_created", offer_id=str(offer.id), title=offer.title, by=str(admin.id))

    return AdminOfferItem(
        id=str(offer.id),
        source=offer.source,
        offer_type=offer.offer_type.value if offer.offer_type else None,
        title=offer.title,
        company=offer.company,
        location=offer.location,
        url=offer.url,
        quality_score=offer.quality_score,
        is_active=offer.is_active,
        has_embedding=False,
        scraped_at=offer.scraped_at,
        posted_at=offer.posted_at,
        expires_at=offer.expires_at,
    )


# ── Création d'utilisateur ─────────────────────────────────────────────────

@router.post("/users", response_model=AdminUserItem, status_code=201)
def create_user(
    payload: AdminUserCreate,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserItem:
    """Crée un compte utilisateur depuis le backoffice."""
    from app.core.security import hash_password
    from app.models.user import PrimaryRole

    # Vérifier doublon email
    if payload.email:
        existing = db.query(User).filter(User.email == payload.email).first()
        if existing:
            raise HTTPException(status_code=409, detail="Un compte avec cet email existe déjà.")

    # Vérifier doublon phone
    if payload.phone:
        existing = db.query(User).filter(User.phone == payload.phone).first()
        if existing:
            raise HTTPException(status_code=409, detail="Un compte avec ce numéro existe déjà.")

    try:
        role_val = UserRole(payload.role)
    except ValueError:
        role_val = UserRole.b2c

    new_user = User(
        email=payload.email,
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        role=role_val,
        is_active=True,
    )
    db.add(new_user)
    db.flush()  # obtenir l'UUID

    pr = None
    if payload.primary_role:
        try:
            pr = PrimaryRole(payload.primary_role)
        except ValueError:
            pass

    profile = Profile(
        user_id=new_user.id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        primary_role=pr,
        country=payload.country,
    )
    db.add(profile)
    db.commit()
    db.refresh(new_user)

    logger.info("admin_user_created", new_user_id=str(new_user.id), by=str(admin.id))

    return _user_item(new_user, profile, 0, 0, 0)


# ── Livrables generes par l'IA ─────────────────────────────────────────────

@router.get("/deliverables", response_model=AdminPaginated[AdminDeliverableItem])
def list_deliverables(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user_id: UUID | None = None,
) -> AdminPaginated[AdminDeliverableItem]:
    """
    Retourne les messages chat qui contiennent des livrables generes par l'IA
    (business plans, CV, plans marketing, QCM, etudes de marche, etc.).
    Ces livrables sont stockes dans payload->deliverables (JSON array).
    """
    from sqlalchemy import cast, text
    from sqlalchemy.dialects.postgresql import JSONB

    base = (
        db.query(ChatMessage, ChatThread, User)
        .join(ChatThread, ChatThread.id == ChatMessage.thread_id)
        .join(User, User.id == ChatThread.user_id)
        .filter(
            ChatMessage.payload.isnot(None),
            ChatMessage.payload["deliverables"].astext.notin_(["null", "[]", ""]),
            ChatMessage.is_active.is_(True),
        )
    )

    if user_id:
        base = base.filter(ChatThread.user_id == user_id)

    count_base = (
        db.query(func.count(ChatMessage.id))
        .join(ChatThread, ChatThread.id == ChatMessage.thread_id)
        .filter(
            ChatMessage.payload.isnot(None),
            ChatMessage.payload["deliverables"].astext.notin_(["null", "[]", ""]),
            ChatMessage.is_active.is_(True),
        )
    )
    if user_id:
        count_base = count_base.filter(ChatThread.user_id == user_id)

    total = count_base.scalar() or 0
    rows = (
        base.order_by(desc(ChatMessage.created_at))
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    items = []
    for msg, thread, user in rows:
        # Extraire le debut du premier livrable
        deliverables = (msg.payload or {}).get("deliverables") or []
        preview = ""
        if deliverables and isinstance(deliverables, list) and len(deliverables) > 0:
            first = deliverables[0] if isinstance(deliverables[0], str) else ""
            preview = first[:300]

        items.append(AdminDeliverableItem(
            message_id=str(msg.id),
            thread_id=str(thread.id),
            thread_title=thread.title,
            user_id=str(user.id),
            user_email=user.email,
            content_preview=preview,
            agent_id=msg.agent_id,
            created_at=msg.created_at,
        ))

    return AdminPaginated(items=items, **_pag(total, page, size))


# ── Malayka Institution — validation des demandes de structure ──────────────

def _structure_item(s: Structure, requester_email: str | None, members_count: int = 0) -> AdminStructureItem:
    return AdminStructureItem(
        id=str(s.id), name=s.name, country=s.country, status=s.status.value,
        structure_type=s.structure_type.value if s.structure_type else None,
        address=s.address, email=s.email,
        requested_by_email=requester_email, created_at=s.created_at,
        members_count=members_count,
    )

@router.get("/structures", response_model=AdminPaginated[AdminStructureItem])
def list_structures(admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)], page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), status: str | None = Query(default="pending")):
    q_obj = db.query(Structure)
    if status:
        try: q_obj = q_obj.filter(Structure.status == StructureStatus(status))
        except ValueError: pass
    total = q_obj.count()
    rows = q_obj.order_by(desc(Structure.created_at)).offset((page-1)*size).limit(size).all()

    items = []
    for s in rows:
        requester = (
            db.query(User)
            .join(StructureMember, StructureMember.user_id == User.id)
            .filter(StructureMember.structure_id == s.id, StructureMember.role == StructureMemberRole.super_admin)
            .first()
        )
        mc = db.query(func.count(StructureMember.id)).filter(StructureMember.structure_id == s.id).scalar() or 0
        items.append(_structure_item(s, requester.email if requester else None, mc))
    return AdminPaginated(items=items, **_pag(total, page, size))

@router.get("/structures/{structure_id}", response_model=AdminStructureDetail)
def get_structure(structure_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    s = db.query(Structure).filter(Structure.id == structure_id).first()
    if not s:
        raise HTTPException(404, "Structure non trouvée")

    # Membres de l'équipe (admins + teachers)
    requester = (
        db.query(User)
        .join(StructureMember, StructureMember.user_id == User.id)
        .filter(StructureMember.structure_id == s.id, StructureMember.role == StructureMemberRole.super_admin)
        .first()
    )
    members_rows = (
        db.query(StructureMember, User, Profile)
        .join(User, User.id == StructureMember.user_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .filter(StructureMember.structure_id == s.id)
        .all()
    )
    members = [
        AdminStructureMember(
            user_id=str(m.user_id), first_name=p.first_name if p else None,
            last_name=p.last_name if p else None, email=u.email, phone=u.phone,
            role=m.role.value, joined_at=m.created_at,
        )
        for m, u, p in members_rows
    ]

    # Classrooms avec stats par salle
    classroom_rows = (
        db.query(Classroom)
        .filter(Classroom.structure_id == s.id)
        .order_by(Classroom.created_at)
        .all()
    )
    classroom_data: list[AdminStructureClassroom] = []
    for c in classroom_rows:
        teachers_count = (
            db.query(func.count(ClassroomTeacher.id))
            .filter(ClassroomTeacher.classroom_id == c.id).scalar() or 0
        )
        students_count = (
            db.query(func.count(ClassroomMembership.id))
            .filter(ClassroomMembership.classroom_id == c.id,
                    ClassroomMembership.status == MembershipStatus.accepted)
            .scalar() or 0
        )
        pending_count = (
            db.query(func.count(ClassroomMembership.id))
            .filter(ClassroomMembership.classroom_id == c.id,
                    ClassroomMembership.status == MembershipStatus.pending_review)
            .scalar() or 0
        )
        courses_count = (
            db.query(func.count(ClassroomCourse.id))
            .filter(ClassroomCourse.classroom_id == c.id).scalar() or 0
        )
        classroom_data.append(AdminStructureClassroom(
            id=str(c.id), name=c.name, invite_code=c.invite_code,
            teachers_count=teachers_count, students_count=students_count,
            pending_members_count=pending_count, courses_count=courses_count,
            created_at=c.created_at,
        ))

    # Invitations enseignants (20 les plus récentes)
    invitation_rows = (
        db.query(StructureInvitation)
        .filter(StructureInvitation.structure_id == s.id)
        .order_by(desc(StructureInvitation.created_at))
        .limit(20)
        .all()
    )
    invitation_data = [
        AdminStructureInvitation(
            id=str(inv.id), first_name=inv.first_name, last_name=inv.last_name,
            contact=inv.contact, status=inv.status.value,
            created_at=inv.created_at, expires_at=inv.expires_at,
        )
        for inv in invitation_rows
    ]

    students_total = sum(c.students_count for c in classroom_data)
    courses_total = sum(c.courses_count for c in classroom_data)
    pending_invitations_count = sum(1 for inv in invitation_data if inv.status == InvitationStatus.pending.value)

    return AdminStructureDetail(
        id=str(s.id), name=s.name, country=s.country, status=s.status.value,
        structure_type=s.structure_type.value if s.structure_type else None,
        structure_type_other=s.structure_type_other,
        address=s.address, email=s.email,
        requested_by_email=requester.email if requester else None,
        created_at=s.created_at, members_count=len(members),
        members=members, classrooms=classroom_data, invitations=invitation_data,
        classrooms_count=len(classroom_data),
        students_total=students_total, courses_total=courses_total,
        pending_invitations_count=pending_invitations_count,
    )

@router.patch("/structures/{structure_id}", response_model=AdminStructureItem)
def update_structure(structure_id: UUID, payload: AdminStructureUpdate, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    s = db.query(Structure).filter(Structure.id == structure_id).first()
    if not s:
        raise HTTPException(404, "Structure non trouvée")
    if payload.status is not None:
        s.status = StructureStatus(payload.status)
    db.commit(); db.refresh(s)
    requester = (
        db.query(User)
        .join(StructureMember, StructureMember.user_id == User.id)
        .filter(StructureMember.structure_id == s.id, StructureMember.role == StructureMemberRole.super_admin)
        .first()
    )
    mc = db.query(func.count(StructureMember.id)).filter(StructureMember.structure_id == s.id).scalar() or 0
    logger.info("admin_structure_updated", structure_id=str(structure_id), by=str(admin.id))
    return _structure_item(s, requester.email if requester else None, mc)

@router.delete("/structures/{structure_id}", status_code=204)
def delete_structure(structure_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    s = db.query(Structure).filter(Structure.id == structure_id).first()
    if not s:
        raise HTTPException(404, "Structure non trouvée")
    db.delete(s)
    db.commit()
    logger.info("admin_structure_deleted", structure_id=str(structure_id), by=str(admin.id))

@router.post("/structures/{structure_id}/approve", response_model=AdminStructureItem)
def approve_structure(structure_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    structure = structure_service.approve_structure(db, structure_id)
    db.commit()
    mc = db.query(func.count(StructureMember.id)).filter(StructureMember.structure_id == structure.id).scalar() or 0
    logger.info("admin_structure_approved", structure_id=str(structure_id), by=str(admin.id))
    return _structure_item(structure, None, mc)

@router.post("/structures/{structure_id}/reject", response_model=AdminStructureItem)
def reject_structure(structure_id: UUID, admin: Annotated[User, Depends(get_admin_user)], db: Annotated[Session, Depends(get_db)]):
    structure = structure_service.reject_structure(db, structure_id)
    db.commit()
    mc = db.query(func.count(StructureMember.id)).filter(StructureMember.structure_id == structure.id).scalar() or 0
    logger.info("admin_structure_rejected", structure_id=str(structure_id), by=str(admin.id))
    return _structure_item(structure, None, mc)

