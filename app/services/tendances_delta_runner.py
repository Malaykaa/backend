"""TendancesDeltaRunner — détecte les changements significatifs dans les tendances
et crée une notification in-app pour chaque user concerné.

Logique t0 → t1 :
  t0 = snapshot précédent stocké en Redis (TTL 8 jours)
  t1 = tendances calculées maintenant

Seuils de déclenchement (au moins 1 suffit) :
  1. offres_pour_toi augmente de +20 % ou +5 offres
  2. Nouveau signal_semaine (label différent de t0)
  3. Nouveau type d'offre apparu dans le pays de l'utilisateur
  4. Compétence clé du user : variation_pts progresse de +10 pts entre t0 et t1
"""
from __future__ import annotations

import json
import logging
import time
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.notification import UserNotification
from app.models.user import Profile, User
from app.models.user_intent import UserIntent

logger = logging.getLogger(__name__)

# TTL snapshot Redis : 8 jours (couvre la semaine + marge)
_SNAPSHOT_TTL = 8 * 24 * 3600
_SNAPSHOT_PREFIX = "trends:snapshot:v1:"

# Seuils de déclenchement
_MIN_OFFER_INCREASE_ABS = 5      # +5 offres absolues
_MIN_OFFER_INCREASE_PCT = 0.20   # +20 %
_MIN_COMP_VARIATION_DELTA = 10   # +10 pts de variation_pts


# ── Redis helpers ─────────────────────────────────────────────────────────────

def _redis():
    """Retourne le client Redis ou None si indisponible."""
    import redis as redis_lib
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        r = redis_lib.from_url(
            settings.redis_url, decode_responses=True,
            socket_timeout=2, socket_connect_timeout=2,
        )
        r.ping()
        return r
    except Exception as exc:
        logger.warning("TendancesDelta: Redis inaccessible — %s", exc)
        return None


def _load_snapshot(user_id: str) -> dict | None:
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(_SNAPSHOT_PREFIX + user_id)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _save_snapshot(user_id: str, snapshot: dict) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.setex(_SNAPSHOT_PREFIX + user_id, _SNAPSHOT_TTL, json.dumps(snapshot))
    except Exception as exc:
        logger.warning("TendancesDelta: erreur sauvegarde snapshot — %s", exc)


# ── Construction du snapshot t1 ───────────────────────────────────────────────

def _build_snapshot(raw: dict, user_context: dict) -> dict:
    """Extrait les métriques pertinentes du résultat trends pour stocker en t0."""
    week = raw.get("week_africa", {})
    mon_pays = raw.get("mon_pays", {})
    competences = raw.get("competences", [])
    globale = raw.get("vue_globale", {})
    signal = globale.get("signal_semaine")

    # Types d'offres présents dans le pays
    pays_types: dict[str, int] = {}
    if mon_pays.get("has_data"):
        for key in ("emplois", "financements", "missions", "appels_offre", "bourses"):
            v = mon_pays.get(key, 0)
            if v and v > 0:
                pays_types[key] = v

    # Compétences clés de l'utilisateur (croisement avec ses skills/goals)
    user_skills = set(s.lower() for s in (user_context.get("skills") or []))
    relevant_comps = []
    for c in competences:
        name = (c.get("competence") or "").lower()
        if user_skills and not any(sk in name or name in sk for sk in user_skills):
            continue
        relevant_comps.append({
            "competence": c.get("competence", ""),
            "variation_pts": c.get("variation_pts", 0),
        })

    return {
        "offres_pour_toi":     week.get("offres_pour_toi"),
        "pour_toi_types":      week.get("pour_toi_types", []),
        "match_local":         mon_pays.get("match_local"),
        "pays":                mon_pays.get("pays", ""),
        "pays_types":          pays_types,
        "signal_label":        signal.get("label") if signal else None,
        "competences":         relevant_comps,
        "snapshot_at":         datetime.now(timezone.utc).isoformat(),
    }


# ── Calcul du delta t0 → t1 ──────────────────────────────────────────────────

def _compute_delta(t0: dict, t1: dict) -> list[dict]:
    """Retourne la liste des changements significatifs. Vide = pas de notif."""
    changes: list[dict] = []

    # 1. offres_pour_toi
    old_offers = t0.get("offres_pour_toi")
    new_offers = t1.get("offres_pour_toi")
    if old_offers is not None and new_offers is not None and new_offers > old_offers:
        delta_abs = new_offers - old_offers
        delta_pct = delta_abs / max(old_offers, 1)
        if delta_abs >= _MIN_OFFER_INCREASE_ABS or delta_pct >= _MIN_OFFER_INCREASE_PCT:
            changes.append({
                "type":    "more_offers",
                "old":     old_offers,
                "new":     new_offers,
                "delta":   delta_abs,
            })

    # 2. Nouveau signal de la semaine
    old_signal = t0.get("signal_label")
    new_signal = t1.get("signal_label")
    if new_signal and new_signal != old_signal:
        changes.append({
            "type":   "new_signal",
            "label":  new_signal,
        })

    # 3. Nouveau type d'offre apparu dans le pays
    old_pays_types = set(t0.get("pays_types", {}).keys())
    new_pays_types = t1.get("pays_types", {})
    for offer_type, count in new_pays_types.items():
        if offer_type not in old_pays_types and count > 0:
            changes.append({
                "type":       "new_country_type",
                "offer_type": offer_type,
                "count":      count,
                "pays":       t1.get("pays", ""),
            })

    # 4. Compétences clés en forte hausse (+10 pts)
    old_comps = {c["competence"]: c["variation_pts"] for c in t0.get("competences", [])}
    for c in t1.get("competences", []):
        old_var = old_comps.get(c["competence"], 0)
        if c["variation_pts"] - old_var >= _MIN_COMP_VARIATION_DELTA:
            changes.append({
                "type":       "competence_surge",
                "competence": c["competence"],
                "variation":  c["variation_pts"],
                "delta_pts":  c["variation_pts"] - old_var,
            })

    return changes


# ── Formatage de la notification ──────────────────────────────────────────────

def _format_title(changes: list[dict]) -> str:
    """Construit le titre de la notification à partir des changements."""
    parts: list[str] = []

    for ch in changes:
        t = ch["type"]
        if t == "more_offers":
            parts.append(
                f"+{ch['delta']} nouvelles offres adaptées à ton profil"
            )
        elif t == "new_signal":
            parts.append(f"Signal : {ch['label']}")
        elif t == "new_country_type":
            labels = {
                "emplois":      "offres d'emploi",
                "financements": "financements",
                "missions":     "missions freelance",
                "appels_offre": "appels à candidature",
                "bourses":      "bourses",
            }
            label = labels.get(ch["offer_type"], ch["offer_type"])
            parts.append(f"Nouvelles {label} au {ch['pays']}")
        elif t == "competence_surge":
            parts.append(
                f"{ch['competence']} en forte hausse (+{ch['delta_pts']} pts)"
            )

    if not parts:
        return "Mise à jour de tes tendances"
    return " · ".join(parts[:2])  # max 2 pour rester concis


# ── Runner principal ──────────────────────────────────────────────────────────

class TendancesDeltaRunner:
    """Vérifie les deltas de tendances pour tous les users éligibles."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def run_once(self) -> dict[str, int]:
        """Lance le cycle complet. Retourne des stats."""
        stats = {"checked": 0, "notified": 0, "errors": 0}

        from app.models.goal import Goal, GoalStatus
        from app.routers.trends import _GOAL_LABELS
        from app.services.trends_service import TrendsService

        # Users éligibles : actifs + au moins 1 objectif actif ou 1 intention récente
        users = self._eligible_users()
        svc = TrendsService(self.db)

        for user in users:
            stats["checked"] += 1
            try:
                # Contexte utilisateur minimal
                user_context = self._build_user_context(user)

                # Calculer t1
                profile = user.profile
                country = (profile.country if profile else None) or None
                goal_types = self._active_goal_types(user.id)
                raw_skills = self._raw_skills(profile)

                if goal_types or raw_skills:
                    raw = svc.get_personalized_summary(
                        user_country=country,
                        user_goal_types=goal_types,
                        user_skills=raw_skills,
                        user_domain=(profile.domain if profile else "") or "",
                    )
                else:
                    raw = svc.get_full_summary(user_country=country)

                t1 = _build_snapshot(raw, user_context)

                # Charger t0
                t0 = _load_snapshot(str(user.id))

                if t0 is None:
                    # Premier passage — on stocke le snapshot sans notifier
                    _save_snapshot(str(user.id), t1)
                    continue

                # Calculer le delta
                changes = _compute_delta(t0, t1)
                if not changes:
                    continue

                # Créer la notification in-app
                title = _format_title(changes)
                notif = UserNotification(
                    user_id=user.id,
                    offer_id=None,
                    offer_title=title,
                    offer_url="/app/tendances",
                    offer_type="tendances_delta",
                    score_pct=None,
                    seen=False,
                )
                self.db.add(notif)
                self.db.flush()

                # Mettre à jour t0 → t1
                _save_snapshot(str(user.id), t1)
                stats["notified"] += 1

                logger.info(
                    "TendancesDelta: notif pour user %s — %d changement(s): %s",
                    user.id, len(changes), [c["type"] for c in changes],
                )

            except Exception:
                stats["errors"] += 1
                logger.exception("TendancesDelta: erreur user %s", user.id)
                self.db.rollback()

        self.db.commit()
        logger.info("TendancesDelta run: %s", stats)
        return stats

    # ── Privé ─────────────────────────────────────────────────────────────────

    def _eligible_users(self) -> list[User]:
        """Users actifs ayant un profil (goals ou intentions récentes)."""
        from datetime import timedelta
        from sqlalchemy import or_
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        users_with_intent = (
            select(UserIntent.user_id)
            .where(UserIntent.extracted_at >= cutoff)
            .distinct()
            .subquery()
        )
        from app.models.goal import Goal, GoalStatus
        users_with_goal = (
            select(Goal.user_id)
            .where(Goal.status == GoalStatus.active)
            .distinct()
            .subquery()
        )

        stmt = (
            select(User)
            .join(Profile, Profile.user_id == User.id)
            .where(
                User.is_active.is_(True),
                or_(
                    User.id.in_(users_with_intent),
                    User.id.in_(users_with_goal),
                ),
            )
        )
        return list(self.db.execute(stmt).scalars().all())

    def _active_goal_types(self, user_id: _uuid.UUID) -> list[str]:
        from app.models.goal import Goal, GoalStatus
        goals = self.db.execute(
            select(Goal)
            .where(Goal.user_id == user_id, Goal.status == GoalStatus.active)
            .limit(5)
        ).scalars().all()
        return [g.type.value for g in goals]

    def _raw_skills(self, profile: Profile | None) -> list[str]:
        if not profile or not profile.skills:
            return []
        if isinstance(profile.skills, list):
            return [str(s) for s in profile.skills if s]
        if isinstance(profile.skills, str):
            return [s.strip() for s in profile.skills.split(",") if s.strip()]
        return []

    def _build_user_context(self, user: User) -> dict:
        profile = user.profile
        skills: list[str] = self._raw_skills(profile)
        return {
            "user_id": str(user.id),
            "skills":  skills,
            "country": (profile.country if profile else "") or "",
            "domain":  (profile.domain if profile else "") or "",
        }
