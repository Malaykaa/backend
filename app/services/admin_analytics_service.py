"""Agrégations du tableau de bord analytique admin.

Toutes les distributions passent par `_bucketize`, toutes les séries par
`_monthly_series` : une seule définition du calcul de pourcentage, du tri, du
regroupement en « Autres » et du remplissage des mois vides. Une correction
faite ici vaut pour les vingt-cinq graphiques du dashboard.

Normalisation géographique — vérifiée sur les données réelles :
- `Profile.country` / `Profile.nationality` contiennent un MÉLANGE de codes ISO
  (« CI », issus de CountrySelect) et de noms complets (« Côte d'Ivoire », saisis
  avant ce composant). Les deux formes désignent le même pays et doivent tomber
  dans la même part, sinon le dashboard affiche deux barres pour un seul pays.
- `ScrapedOffer.location` est du texte libre issu du scraping (« Abidjan, Côte
  d'Ivoire »), normalisé via la table géographique de trends_service.

Les trois formes convergent vers un nom canonique unique dans `_geo_country`.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatThread
from app.models.document import Document
from app.models.goal import Goal
from app.models.scraped_offer import ScrapedOffer
from app.models.user import Profile, User
from app.models.user_intent import UserIntent
from app.schemas.admin_analytics import (
    AdminAnalytics,
    Bucket,
    EngagementAnalytics,
    GoalsAnalytics,
    IntentsAnalytics,
    Kpi,
    OffersAnalytics,
    SeriesPoint,
    UsersAnalytics,
)
# Réutilisation délibérée : _AFRICAN_GEO est une table de ~100 entrées déjà
# maintenue pour les Tendances. La redéclarer ici garantirait une divergence.
from app.services.trends_service import _AFRICAN_GEO, _normalize

# Nombre de parts affichées avant regroupement dans « Autres ». Au-delà, un
# graphique devient illisible sans rien apprendre de plus.
_TOP_N = 10

# Plafond de lignes chargées en mémoire pour les agrégations faites en Python
# (mots-clés et presets, stockés en JSON donc non agrégeables en SQL portable).
_PY_AGG_LIMIT = 20_000

_MONTH_ABBR_FR = [
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
]

# Correspondance code ISO alpha-2 → nom canonique.
#
# `Profile.country` contient un MÉLANGE : certains enregistrements portent le
# code renvoyé par CountrySelect (« CI »), d'autres le nom complet saisi avant
# ce composant (« Côte d'Ivoire »). Sans cette table, le même pays produit deux
# barres distinctes dans le dashboard.
#
# Généré depuis frontend/src/shared/data/countries.ts — régénérer si cette liste change.
_ISO_ALPHA2: dict[str, str] = {
    "CI": "Côte d'Ivoire",
    "SN": "Sénégal",
    "ML": "Mali",
    "BF": "Burkina Faso",
    "GN": "Guinée",
    "GH": "Ghana",
    "NG": "Nigéria",
    "TG": "Togo",
    "BJ": "Bénin",
    "NE": "Niger",
    "MR": "Mauritanie",
    "GM": "Gambie",
    "GW": "Guinée-Bissau",
    "SL": "Sierra Leone",
    "LR": "Libéria",
    "CM": "Cameroun",
    "CD": "RD Congo",
    "CG": "Congo",
    "GA": "Gabon",
    "CF": "Centrafrique",
    "TD": "Tchad",
    "GQ": "Guinée équatoriale",
    "ET": "Éthiopie",
    "KE": "Kenya",
    "TZ": "Tanzanie",
    "UG": "Ouganda",
    "RW": "Rwanda",
    "BI": "Burundi",
    "DJ": "Djibouti",
    "SO": "Somalie",
    "MA": "Maroc",
    "DZ": "Algérie",
    "TN": "Tunisie",
    "LY": "Libye",
    "EG": "Égypte",
    "ZA": "Afrique du Sud",
    "ZW": "Zimbabwe",
    "ZM": "Zambie",
    "MG": "Madagascar",
    "FR": "France",
    "BE": "Belgique",
    "CH": "Suisse",
    "CA": "Canada",
    "US": "États-Unis",
    "GB": "Royaume-Uni",
    "DE": "Allemagne",
    "ES": "Espagne",
    "IT": "Italie",
    "PT": "Portugal",
}

_UNKNOWN_KEY = "unknown"
_UNKNOWN_LABEL = "Non renseigné"

_GENDER_LABELS = {"M": "Homme", "F": "Femme", "other": "Autre"}
_ROLE_LABELS = {
    "student": "Étudiant",
    "job_seeker": "Demandeur d'emploi",
    "professional": "Professionnel",
}
_GOAL_STATUS_LABELS = {"active": "Actif", "completed": "Terminé", "paused": "En pause"}
_OFFER_TYPE_LABELS = {
    "job": "Emploi",
    "scholarship": "Bourse",
    "grant": "Financement",
    "call_for_applications": "Appel à candidature",
    "opportunity": "Opportunité",
    "formation": "Formation",
    "partnership": "Partenariat",
    "resource": "Ressource",
}
_GOAL_TYPE_LABELS = {
    "career": "Carrière",
    "scholarship": "Bourse",
    "study_grant": "Aide aux études",
    "funding": "Financement",
    "tender": "Appel d'offres",
    "freelance": "Freelance",
    "exam": "Examen",
    "orientation": "Orientation",
    "coursework": "Suivi scolaire",
}
_QUALITY_LABELS = {
    "high": "Élevée (≥ 75)",
    "medium": "Moyenne (50–74)",
    "low": "Faible (< 50)",
    _UNKNOWN_KEY: "Non calculée",
}
_AGE_LABELS = {
    "u18": "moins de 18",
    "18_24": "18–24",
    "25_34": "25–34",
    "35_44": "35–44",
    "45_54": "45–54",
    "55p": "55 et +",
    _UNKNOWN_KEY: _UNKNOWN_LABEL,
}


# ── Primitives ───────────────────────────────────────────────────────────────


def _bucketize(
    rows: list[tuple],
    *,
    labels: dict[str, str] | None = None,
    top_n: int = _TOP_N,
    normalize_geo: bool = False,
) -> list[Bucket]:
    """Transforme des couples (valeur, compte) en distribution ordonnée.

    - Les valeurs nulles ou vides sont regroupées sous une part « Non renseigné »
      plutôt qu'écartées : un taux de remplissage faible est une information,
      la masquer donnerait une lecture faussement propre.
    - Au-delà de `top_n`, la queue est agrégée en « Autres » pour garder les
      graphiques lisibles sans perdre le total.
    - Les pourcentages sont calculés sur le total AVANT troncature, donc ils
      somment toujours à 100.
    """
    counts: Counter = Counter()
    for value, count in rows:
        raw = value.value if hasattr(value, "value") else value
        key = str(raw).strip() if raw is not None and str(raw).strip() else _UNKNOWN_KEY
        if normalize_geo and key != _UNKNOWN_KEY:
            key = _geo_country(key)
        counts[key] += int(count or 0)

    total = sum(counts.values())
    if total == 0:
        return []

    def _label(key: str) -> str:
        if key == _UNKNOWN_KEY:
            return _UNKNOWN_LABEL
        return (labels or {}).get(key, key)

    # « Non renseigné » est sorti du classement : il termine toujours la liste,
    # quelle que soit sa taille, pour ne pas occuper la tête d'un graphique.
    unknown = counts.pop(_UNKNOWN_KEY, 0)
    ranked = counts.most_common()

    head = ranked[:top_n]
    tail_count = sum(c for _, c in ranked[top_n:])

    buckets = [
        Bucket(key=k, label=_label(k), count=c, pct=round(c / total * 100, 1))
        for k, c in head
    ]
    if tail_count:
        buckets.append(
            Bucket(key="_others", label="Autres", count=tail_count,
                   pct=round(tail_count / total * 100, 1))
        )
    if unknown:
        buckets.append(
            Bucket(key=_UNKNOWN_KEY, label=_UNKNOWN_LABEL, count=unknown,
                   pct=round(unknown / total * 100, 1))
        )
    return buckets


def _geo_country(location: str) -> str:
    """Ramène une valeur géographique — code ISO, nom, ou adresse — à un pays unique.

    Trois formes coexistent en base et doivent converger, sinon un même pays
    occupe deux barres du graphique :
      « CI »                      → code ISO (CountrySelect)
      « Côte d'Ivoire »           → nom saisi avant ce composant
      « Abidjan, Côte d'Ivoire »  → texte libre du scraping

    Le code ISO est testé en premier car il est non ambigu. `_AFRICAN_GEO` est
    trié du plus long au plus court, donc « niger » ne peut pas matcher avant
    « nigeria ». Une valeur non reconnue est conservée telle quelle si elle est
    courte (probablement déjà un pays), sinon elle bascule en « Non renseigné » :
    une adresse complète n'est pas une dimension d'analyse exploitable.
    """
    stripped = location.strip()
    iso = _ISO_ALPHA2.get(stripped.upper())
    if iso:
        return iso

    norm = _normalize(stripped)
    for key, display in _AFRICAN_GEO:
        if key in norm:
            return display
    return stripped if len(stripped) < 40 else _UNKNOWN_KEY


def _month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_label(dt: datetime) -> str:
    return f"{_MONTH_ABBR_FR[dt.month - 1]} {dt.year % 100:02d}"


def _window_start(now: datetime, months: int) -> datetime:
    """Début du premier mois de la fenêtre, à minuit UTC.

    On tronque au mois pour que la série commence sur un mois entier — sinon le
    premier point serait un mois partiel et afficherait une fausse chute.
    """
    year, month = now.year, now.month
    total = (year * 12 + month - 1) - (months - 1)
    return datetime(total // 12, total % 12 + 1, 1, tzinfo=timezone.utc)


def _monthly_series(
    db: Session, date_col, *, months: int, now: datetime, extra_filter=None,
) -> list[SeriesPoint]:
    """Compte par mois sur la fenêtre, mois vides inclus.

    Les mois sans donnée sont renvoyés à zéro plutôt qu'omis : une série à trous
    se dessine comme une courbe continue et masque les périodes creuses.
    """
    start = _window_start(now, months)
    bucket = func.date_trunc("month", date_col).label("m")
    stmt = select(bucket, func.count()).where(date_col >= start)
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)
    rows = db.execute(stmt.group_by(bucket)).all()

    counts = {_month_key(m): int(c) for m, c in rows if m is not None}

    series: list[SeriesPoint] = []
    cursor = start
    while cursor <= now:
        key = _month_key(cursor)
        series.append(
            SeriesPoint(period=key, label=_month_label(cursor), count=counts.get(key, 0))
        )
        cursor = (
            datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc)
            if cursor.month == 12
            else datetime(cursor.year, cursor.month + 1, 1, tzinfo=timezone.utc)
        )
    return series


def _kpi(db, model, date_col, *, months: int, now: datetime) -> Kpi:
    """Cumul total, volume de la fenêtre, et comparaison à la fenêtre précédente."""
    start = _window_start(now, months)
    prev_start = _window_start(start - timedelta(seconds=1), months)

    total = int(db.execute(select(func.count(model.id))).scalar() or 0)
    current = int(
        db.execute(select(func.count(model.id)).where(date_col >= start)).scalar() or 0
    )
    previous = int(
        db.execute(
            select(func.count(model.id)).where(date_col >= prev_start, date_col < start)
        ).scalar() or 0
    )
    # Une progression depuis zéro n'a pas de pourcentage défini — renvoyer un
    # nombre arbitraire (0 ou 100) tromperait la lecture.
    variation = round((current - previous) / previous * 100, 1) if previous else None
    return Kpi(total=total, current=current, previous=previous, variation_pct=variation)


def _age_bracket_expr(now: datetime):
    """Tranche d'âge dérivée de `birth_year`, calculée en SQL."""
    age = cast(now.year, Integer) - Profile.birth_year
    return case(
        (Profile.birth_year.is_(None), _UNKNOWN_KEY),
        (age < 18, "u18"),
        (age < 25, "18_24"),
        (age < 35, "25_34"),
        (age < 45, "35_44"),
        (age < 55, "45_54"),
        else_="55p",
    )


def _group_count(db: Session, expr, *, join=None, where=None) -> list[tuple]:
    """GROUP BY générique renvoyant des couples (valeur, compte)."""
    stmt = select(expr, func.count())
    if join is not None:
        stmt = stmt.select_from(join)
    if where is not None:
        stmt = stmt.where(where)
    return db.execute(stmt.group_by(expr)).all()


# ── Sections ─────────────────────────────────────────────────────────────────


class AdminAnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build(self, months: int = 12) -> AdminAnalytics:
        now = datetime.now(timezone.utc)
        return AdminAnalytics(
            generated_at=now,
            months=months,
            users=self._users(months, now),
            offers=self._offers(months, now),
            intents=self._intents(months, now),
            goals=self._goals(months, now),
            engagement=self._engagement(months, now),
        )

    # ── Utilisateurs ─────────────────────────────────────────────────────────

    def _users(self, months: int, now: datetime) -> UsersAnalytics:
        db = self.db
        # Jointure externe : un utilisateur sans profil doit compter dans les
        # totaux et alimenter la part « Non renseigné », pas disparaître.
        joined = User.__table__.outerjoin(Profile.__table__, Profile.user_id == User.id)

        complete_expr = case(
            (
                Profile.country.isnot(None)
                & Profile.city.isnot(None)
                & Profile.nationality.isnot(None)
                & Profile.gender.isnot(None)
                & Profile.birth_year.isnot(None),
                "complete",
            ),
            else_="incomplete",
        )

        return UsersAnalytics(
            kpi=_kpi(db, User, User.created_at, months=months, now=now),
            monthly=_monthly_series(db, User.created_at, months=months, now=now),
            by_country=_bucketize(
                _group_count(db, Profile.country, join=joined), normalize_geo=True
            ),
            by_nationality=_bucketize(
                _group_count(db, Profile.nationality, join=joined), normalize_geo=True
            ),
            by_gender=_bucketize(
                _group_count(db, Profile.gender, join=joined), labels=_GENDER_LABELS
            ),
            by_age_bracket=_bucketize(
                _group_count(db, _age_bracket_expr(now), join=joined),
                labels=_AGE_LABELS, top_n=6,
            ),
            by_domain=_bucketize(_group_count(db, Profile.domain, join=joined)),
            by_role=_bucketize(
                _group_count(db, Profile.primary_role, join=joined), labels=_ROLE_LABELS
            ),
            by_city=_bucketize(_group_count(db, Profile.city, join=joined)),
            by_language=_bucketize(_group_count(db, Profile.language, join=joined)),
            profile_completion=_bucketize(
                _group_count(db, complete_expr, join=joined),
                labels={"complete": "Profil complet", "incomplete": "Profil incomplet"},
                top_n=2,
            ),
        )

    # ── Offres ───────────────────────────────────────────────────────────────

    def _offers(self, months: int, now: datetime) -> OffersAnalytics:
        db = self.db
        quality_expr = case(
            (ScrapedOffer.quality_score.is_(None), _UNKNOWN_KEY),
            (ScrapedOffer.quality_score >= 75, "high"),
            (ScrapedOffer.quality_score >= 50, "medium"),
            else_="low",
        )

        total = int(db.execute(select(func.count(ScrapedOffer.id))).scalar() or 0)
        indexed = int(
            db.execute(
                select(func.count(ScrapedOffer.id)).where(ScrapedOffer.embedding.isnot(None))
            ).scalar() or 0
        )

        return OffersAnalytics(
            kpi=_kpi(db, ScrapedOffer, ScrapedOffer.scraped_at, months=months, now=now),
            monthly=_monthly_series(db, ScrapedOffer.scraped_at, months=months, now=now),
            by_country=_bucketize(
                _group_count(db, ScrapedOffer.location), normalize_geo=True
            ),
            by_type=_bucketize(
                _group_count(db, ScrapedOffer.offer_type), labels=_OFFER_TYPE_LABELS
            ),
            by_source=_bucketize(_group_count(db, ScrapedOffer.source)),
            by_quality=_bucketize(
                _group_count(db, quality_expr), labels=_QUALITY_LABELS, top_n=4
            ),
            active_count=int(
                db.execute(
                    select(func.count(ScrapedOffer.id)).where(ScrapedOffer.is_active.is_(True))
                ).scalar() or 0
            ),
            indexed_count=indexed,
            indexed_pct=round(indexed / total * 100, 1) if total else 0.0,
        )

    # ── Intentions ───────────────────────────────────────────────────────────

    def _intents(self, months: int, now: datetime) -> IntentsAnalytics:
        db = self.db
        # Croisement intention × profil : c'est ce qui permet de lire « qui »
        # cherche « quoi », la question à laquelle des comptes séparés ne
        # répondent pas.
        joined = UserIntent.__table__.outerjoin(
            Profile.__table__, Profile.user_id == UserIntent.user_id
        )

        keyword_rows = db.execute(
            select(UserIntent.keywords)
            .where(UserIntent.keywords.isnot(None))
            .order_by(UserIntent.extracted_at.desc())
            .limit(_PY_AGG_LIMIT)
        ).scalars().all()
        kw_counter: Counter = Counter()
        for kws in keyword_rows:
            if not isinstance(kws, list):
                continue
            # Dédupliqué par intention : un mot répété dans une même intention
            # ne doit pas peser plus qu'un mot cité par deux utilisateurs.
            for kw in {str(k).strip().lower() for k in kws if k and str(k).strip()}:
                kw_counter[kw] += 1

        return IntentsAnalytics(
            kpi=_kpi(db, UserIntent, UserIntent.extracted_at, months=months, now=now),
            monthly=_monthly_series(db, UserIntent.extracted_at, months=months, now=now),
            by_type=_bucketize(_group_count(db, UserIntent.intent_type)),
            by_domain=_bucketize(_group_count(db, UserIntent.domain)),
            by_location=_bucketize(
                _group_count(db, UserIntent.location), normalize_geo=True
            ),
            by_level=_bucketize(_group_count(db, UserIntent.level)),
            top_keywords=_bucketize(
                list(kw_counter.items()), top_n=20
            ),
            by_user_gender=_bucketize(
                _group_count(db, Profile.gender, join=joined), labels=_GENDER_LABELS
            ),
            by_user_country=_bucketize(
                _group_count(db, Profile.country, join=joined), normalize_geo=True
            ),
            by_user_nationality=_bucketize(
                _group_count(db, Profile.nationality, join=joined), normalize_geo=True
            ),
        )

    # ── Objectifs ────────────────────────────────────────────────────────────

    def _goals(self, months: int, now: datetime) -> GoalsAnalytics:
        db = self.db
        joined = Goal.__table__.outerjoin(Profile.__table__, Profile.user_id == Goal.user_id)

        # `context_data` est du JSON (pas JSONB) : l'agrégation SQL ne serait pas
        # portable, on compte donc en Python sur une fenêtre bornée.
        preset_rows = db.execute(
            select(Goal.context_data)
            .where(Goal.context_data.isnot(None))
            .order_by(Goal.created_at.desc())
            .limit(_PY_AGG_LIMIT)
        ).scalars().all()
        preset_counter: Counter = Counter()
        for ctx in preset_rows:
            if isinstance(ctx, dict) and ctx.get("preset_key"):
                preset_counter[str(ctx["preset_key"])] += 1

        return GoalsAnalytics(
            kpi=_kpi(db, Goal, Goal.created_at, months=months, now=now),
            monthly=_monthly_series(db, Goal.created_at, months=months, now=now),
            by_type=_bucketize(_group_count(db, Goal.type), labels=_GOAL_TYPE_LABELS),
            by_status=_bucketize(
                _group_count(db, Goal.status), labels=_GOAL_STATUS_LABELS, top_n=3
            ),
            by_preset=_bucketize(list(preset_counter.items())),
            by_user_country=_bucketize(
                _group_count(db, Profile.country, join=joined), normalize_geo=True
            ),
            by_user_gender=_bucketize(
                _group_count(db, Profile.gender, join=joined), labels=_GENDER_LABELS
            ),
            by_user_city=_bucketize(_group_count(db, Profile.city, join=joined)),
        )

    # ── Engagement ───────────────────────────────────────────────────────────

    def _engagement(self, months: int, now: datetime) -> EngagementAnalytics:
        db = self.db
        users_total = int(db.execute(select(func.count(User.id))).scalar() or 0)
        goals_total = int(db.execute(select(func.count(Goal.id))).scalar() or 0)
        users_with_goal = int(
            db.execute(select(func.count(func.distinct(Goal.user_id)))).scalar() or 0
        )
        users_with_intent = int(
            db.execute(select(func.count(func.distinct(UserIntent.user_id)))).scalar() or 0
        )

        return EngagementAnalytics(
            threads_total=int(db.execute(select(func.count(ChatThread.id))).scalar() or 0),
            messages_total=int(db.execute(select(func.count(ChatMessage.id))).scalar() or 0),
            documents_total=int(db.execute(select(func.count(Document.id))).scalar() or 0),
            messages_monthly=_monthly_series(
                db, ChatMessage.created_at, months=months, now=now
            ),
            documents_monthly=_monthly_series(
                db, Document.created_at, months=months, now=now
            ),
            avg_goals_per_user=round(goals_total / users_total, 2) if users_total else 0.0,
            users_with_goal_pct=(
                round(users_with_goal / users_total * 100, 1) if users_total else 0.0
            ),
            users_with_intent_pct=(
                round(users_with_intent / users_total * 100, 1) if users_total else 0.0
            ),
        )
