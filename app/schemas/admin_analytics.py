"""Schémas du tableau de bord analytique admin.

Trois primitives seulement — une distribution (`Bucket`), une série temporelle
(`SeriesPoint`) et un indicateur avec comparaison (`Kpi`) — réutilisées pour
toutes les dimensions. Le frontend n'a donc qu'un jeu de composants
graphiques à écrire, quelle que soit la donnée affichée.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Bucket(BaseModel):
    """Une part d'une distribution (une barre, un secteur)."""

    key: str = Field(description="Valeur brute en base — sert de clé de rendu et de filtre.")
    label: str = Field(description="Libellé lisible. Égal à `key` quand le frontend doit le résoudre lui-même (codes pays).")
    count: int
    pct: float = Field(description="Part du total de la dimension, en pourcentage arrondi à 0,1.")


class SeriesPoint(BaseModel):
    """Un point d'une série mensuelle. Les mois sans donnée valent 0, jamais absents."""

    period: str = Field(description="Mois au format AAAA-MM.")
    label: str = Field(description="Libellé court pour l'axe, ex. « janv. 26 ».")
    count: int


class Kpi(BaseModel):
    """Indicateur avec comparaison à la période précédente de même durée."""

    total: int = Field(description="Cumul depuis toujours.")
    current: int = Field(description="Sur la fenêtre analysée.")
    previous: int = Field(description="Sur la fenêtre de même durée qui la précède.")
    variation_pct: float | None = Field(
        default=None,
        description="Variation en %. NULL si la période précédente est à zéro — "
                    "une croissance depuis zéro n'a pas de pourcentage défini.",
    )


class UsersAnalytics(BaseModel):
    kpi: Kpi
    monthly: list[SeriesPoint]
    by_country: list[Bucket]
    by_nationality: list[Bucket]
    by_gender: list[Bucket]
    by_age_bracket: list[Bucket]
    by_domain: list[Bucket]
    by_role: list[Bucket]
    by_city: list[Bucket]
    by_language: list[Bucket]
    profile_completion: list[Bucket]


class OffersAnalytics(BaseModel):
    kpi: Kpi
    monthly: list[SeriesPoint]
    by_country: list[Bucket]
    by_type: list[Bucket]
    by_source: list[Bucket]
    by_quality: list[Bucket]
    active_count: int
    indexed_count: int
    indexed_pct: float


class IntentsAnalytics(BaseModel):
    kpi: Kpi
    monthly: list[SeriesPoint]
    by_type: list[Bucket]
    by_domain: list[Bucket]
    by_location: list[Bucket]
    by_level: list[Bucket]
    top_keywords: list[Bucket]
    by_user_gender: list[Bucket]
    by_user_country: list[Bucket]
    by_user_nationality: list[Bucket]


class GoalsAnalytics(BaseModel):
    kpi: Kpi
    monthly: list[SeriesPoint]
    by_type: list[Bucket]
    by_status: list[Bucket]
    by_preset: list[Bucket]
    by_user_country: list[Bucket]
    by_user_gender: list[Bucket]
    by_user_city: list[Bucket]


class EngagementAnalytics(BaseModel):
    """Signaux d'usage — complètent les volumes bruts."""

    threads_total: int
    messages_total: int
    documents_total: int
    messages_monthly: list[SeriesPoint]
    documents_monthly: list[SeriesPoint]
    avg_goals_per_user: float
    users_with_goal_pct: float
    users_with_intent_pct: float


class AdminAnalytics(BaseModel):
    generated_at: datetime
    months: int = Field(description="Profondeur de la fenêtre analysée, en mois.")
    users: UsersAnalytics
    offers: OffersAnalytics
    intents: IntentsAnalytics
    goals: GoalsAnalytics
    engagement: EngagementAnalytics
