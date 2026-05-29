"""Router Tendances — données agrégées + interprétations LLM personnalisées."""
from __future__ import annotations

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import extract_profile, get_current_user
from app.models.goal import Goal, GoalStatus
from app.models.user import User
from app.models.user_intent import UserIntent
from app.services.trends_service import TrendsService
from app.services.trends_llm_service import TrendsLLMService

router = APIRouter(prefix="/trends", tags=["trends"])

# Mapping GoalType → libellé lisible pour le LLM
_GOAL_LABELS = {
    "career":       "emploi / évolution de carrière",
    "scholarship":  "bourse d'études",
    "study_grant":  "financement d'études",
    "funding":      "financement / subvention",
    "tender":       "appel à candidature / appel d'offres",
    "freelance":    "missions freelance / consulting",
    "exam":         "préparation concours / examen",
}


@router.get("/summary")
async def get_trends_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Retourne données agrégées + interprétations LLM personnalisées.

    Si l'utilisateur a des goals ou des compétences déclarées, les agrégations
    SQL sont enrichies avec ses chiffres personnels (offres pour lui, verdict pays,
    compétences matchées, orientations carrière). Sinon fallback vers les données brutes.
    Si le LLM est indisponible, le frontend bascule sur les templates locaux.
    """
    profile = extract_profile(current_user)
    user_country = profile.get("country") or profile.get("location") or None

    # Goals actifs — on les récupère une seule fois pour SQL + LLM context
    active_goals_rows = db.execute(
        select(Goal)
        .where(Goal.user_id == current_user.id, Goal.status == GoalStatus.active)
        .order_by(Goal.created_at.desc())
        .limit(5)
    ).scalars().all()

    user_goal_types = [g.type.value for g in active_goals_rows]

    # Skills — stockées en String("200") comma-separated ou liste
    raw_skills = profile.get("skills") or []
    if isinstance(raw_skills, str):
        user_skills = [s.strip() for s in raw_skills.split(",") if s.strip()]
    else:
        user_skills = list(raw_skills)

    user_domain = profile.get("domain") or ""

    # Agrégations SQL avec enrichissement si profil renseigné
    svc = TrendsService(db)
    if user_goal_types or user_skills:
        raw = svc.get_personalized_summary(
            user_country=user_country,
            user_goal_types=user_goal_types,
            user_skills=user_skills,
            user_domain=user_domain,
        )
    else:
        raw = svc.get_full_summary(user_country=user_country)

    # Contexte utilisateur complet pour le LLM — 5 intentions récentes agrégées
    recent_intents = db.execute(
        select(UserIntent)
        .where(UserIntent.user_id == current_user.id)
        .order_by(UserIntent.extracted_at.desc())
        .limit(5)
    ).scalars().all()

    # Fréquence des types : ex. {"emploi": 3, "bourse": 1} → ["emploi", "bourse"]
    _type_counter: Counter = Counter(
        i.intent_type for i in recent_intents if i.intent_type
    )
    intent_types: list[str] = [t for t, _ in _type_counter.most_common()]

    # Domaines uniques, ordre chronologique inverse (plus récent en premier)
    _seen_domains: set[str] = set()
    intent_domains: list[str] = []
    for _i in recent_intents:
        if _i.domain and _i.domain not in _seen_domains:
            _seen_domains.add(_i.domain)
            intent_domains.append(_i.domain)

    # Mots-clés fusionnés et dédupliqués, triés par fréquence
    _kw_counter: Counter = Counter()
    for _i in recent_intents:
        for kw in (_i.keywords or []):
            if kw:
                _kw_counter[str(kw)] += 1
    intent_keywords: list[str] = [kw for kw, _ in _kw_counter.most_common(10)]

    # Résumés des 3 intentions les plus récentes
    intent_summaries: list[str] = [_i.intent_summary for _i in recent_intents[:3]]

    user_context = {
        "user_id":          str(current_user.id),
        "primary_role":     profile.get("primary_role") or "",
        "domain":           user_domain,
        "field_of_study":   profile.get("field_of_study") or "",
        "current_status":   profile.get("current_status") or "",
        "country":          profile.get("country") or "",
        "skills":           user_skills,
        "active_goals":     [
            _GOAL_LABELS.get(g.type.value, g.type.value) for g in active_goals_rows
        ],
        # Intentions agrégées (remplacent les champs singuliers précédents)
        "intent_summaries": intent_summaries,   # résumés libres, plus récent en premier
        "intent_types":     intent_types,       # types ordonnés par fréquence
        "intent_domains":   intent_domains,     # domaines uniques, plus récent en premier
        "intent_keywords":  intent_keywords,    # mots-clés fusionnés par fréquence
    }

    # Interprétations LLM personnalisées — asynchrone, un seul appel
    interpretations = await TrendsLLMService().interpret_all(raw, user_context=user_context)
    if interpretations:
        _attach(raw, interpretations)

    return raw


# ── Attachement des interprétations aux données brutes ───────────────────────

def _attach(raw: dict, interp: dict) -> None:
    """Attache les interprétations LLM aux données brutes (in-place)."""

    # Bloc 1 — tendances : match par type
    by_type = {t["type"]: t for t in interp.get("tendances", [])}
    for tendance in raw["week_africa"]["tendances"]:
        ti = by_type.get(tendance["type"])
        if ti:
            tendance["interpretation"] = {
                "ce_qui_se_passe": ti.get("ce_qui_se_passe", ""),
                "signification":   ti.get("signification", ""),
                "action":          ti.get("action", ""),
            }

    # Bloc 2 — mon pays
    mp = interp.get("mon_pays")
    if mp:
        raw["mon_pays"]["interpretation"] = {
            "emplois":      mp.get("emplois", ""),
            "financements": mp.get("financements", ""),
            "missions":     mp.get("missions", ""),
            "appels_offre": mp.get("appels_offre", ""),
            "bourses":      mp.get("bourses", ""),
        }

    # Bloc 3 — compétences : match par nom
    by_comp = {c["competence"]: c for c in interp.get("competences", [])}
    for comp in raw["competences"]:
        ci = by_comp.get(comp["competence"])
        if ci:
            comp["interpretation"] = {
                "pourquoi":      ci.get("pourquoi", ""),
                "si_tu_as":      ci.get("si_tu_as", ""),
                "si_tu_nas_pas": ci.get("si_tu_nas_pas", ""),
            }

    # Bloc 4 — vue globale
    vg = interp.get("vue_globale")
    if vg:
        raw["vue_globale"]["interpretation"] = {
            "geographie":     vg.get("geographie", ""),
            "repartition":    vg.get("repartition", ""),
            "signal":         vg.get("signal"),
            "action_globale": vg.get("action_globale", ""),
        }
