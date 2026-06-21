"""Mapping presetKey (frontend) → GoalType (backend).

Single source of truth utilisée par le frontend_adapter et les agents.
Ajouter un nouveau topic = ajouter UNE ligne dans chaque dict.
"""

from __future__ import annotations

from app.models.goal import GoalType

# ── presetKey → GoalType ────────────────────────────────────

PRESET_TO_GOAL_TYPE: dict[str, GoalType] = {
    # ── Objectifs v2 (frontend NewObjectiveSheet) ──────────────────────────
    "stage_emploi":          GoalType.career,
    "bourse_etudes":         GoalType.scholarship,
    "subvention_financement": GoalType.funding,
    "prepa_exam":            GoalType.exam,
    "appel_offres":          GoalType.tender,
    "missions_freelance":    GoalType.freelance,
    "appels_projet":         GoalType.study_grant,
    "orientation_carriere":  GoalType.orientation,
    "soutien_scolaire":      GoalType.coursework,

    # ── Mapping direct des noms GoalType (compat frontend qui envoyait le nom) ──
    "career":      GoalType.career,
    "scholarship":  GoalType.scholarship,
    "funding":      GoalType.funding,
    "exam":         GoalType.exam,
    "tender":       GoalType.tender,
    "freelance":    GoalType.freelance,
    "study_grant":  GoalType.study_grant,
    "orientation":  GoalType.orientation,
    "coursework":   GoalType.coursework,
    "professional": GoalType.career,

    # ── Legacy topics (données existantes en DB) ───────────────────────────
    "topic1":  GoalType.career,
    "topic2":  GoalType.career,
    "topic3":  GoalType.career,
    "topic4":  GoalType.tender,
    "topic5":  GoalType.exam,
    "topic6":  GoalType.career,
    "topic7":  GoalType.scholarship,
    "topic8":  GoalType.career,
    "topic9":  GoalType.career,
    "topic10": GoalType.study_grant,
    "topic11": GoalType.career,
    "topic12": GoalType.career,
    "topic13": GoalType.career,
    "topic14": GoalType.exam,
    "topic15": GoalType.funding,
    "topic16": GoalType.tender,
    "topic17": GoalType.funding,
    "topic18": GoalType.career,
    "topic19": GoalType.tender,
    "topic20": GoalType.freelance,
}

# ── presetKey → label affiché ───────────────────────────────

PRESET_LABELS: dict[str, str] = {
    # Objectifs v2
    "stage_emploi":           "Stage / Emploi",
    "bourse_etudes":          "Bourse d'Études · Recherches",
    "subvention_financement": "Subvention / Financement projet",
    "prepa_exam":             "Prépa Exam / Concours",
    "appel_offres":           "Appel d'offres / À proposition",
    "missions_freelance":     "Missions Freelances",
    "appels_projet":          "Appels à Projet / Candidature",
    "orientation_carriere":   "Orientation de Carrière",
    "soutien_scolaire":       "Suivi Scolaire / Cours",
    # Legacy topics
    "topic1":  "Développer une Nouvelle Compétence",
    "topic2":  "Booster ma Carrière",
    "topic3":  "Trouver Stage / Emploi",
    "topic4":  "Appels d'Offres / À Proposition",
    "topic5":  "Rédiger Mémoire / Rapport",
    "topic6":  "Parler une Langue",
    "topic7":  "Trouver Bourse d'Étude",
    "topic8":  "Faire une Reconversion",
    "topic9":  "Vie Personnelle",
    "topic10": "Aventure et Découverte",
    "topic11": "Leadership & Soft Skills",
    "topic12": "Éducation Financière",
    "topic13": "Droit et Devoir",
    "topic14": "Préparer Examens / Concours",
    "topic15": "Gérer Entreprise",
    "topic16": "Prospection Commerciale",
    "topic17": "Rechercher Financements",
    "topic18": "Orientation Carrière",
    "topic19": "Partenariats",
    "topic20": "Missions Freelance",
}

# ── GoalType → source affichée dans le frontend ────────────

_GOAL_TYPE_SOURCE: dict[GoalType, str] = {
    GoalType.career:      "Emploi & Carrière",
    GoalType.exam:        "Examens & Concours",
    GoalType.scholarship: "Bourse & Recherche",
    GoalType.funding:     "Financement",
    GoalType.tender:      "Appels d'offres",
    GoalType.study_grant: "Appels à candidature",
    GoalType.freelance:   "Freelance",
    GoalType.orientation: "Orientation",
    GoalType.coursework:  "Suivi scolaire",
}


def get_goal_type(preset_key: str) -> GoalType:
    """Retourne le GoalType pour un presetKey. Fallback : career."""
    return PRESET_TO_GOAL_TYPE.get(preset_key, GoalType.career)


def get_preset_label(preset_key: str) -> str:
    """Retourne le label affiché pour un presetKey."""
    return PRESET_LABELS.get(preset_key, preset_key)


def get_source_label(goal_type: GoalType) -> str:
    """Retourne le label 'source' affiché côté frontend pour un GoalType."""
    return _GOAL_TYPE_SOURCE.get(goal_type, "99eAnge")
