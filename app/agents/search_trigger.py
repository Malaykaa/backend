"""Logique de déclenchement de la recherche web temps réel.

Module isolé et testable indépendamment.
"""

from __future__ import annotations

# ── Goal types éligibles → modèle Perplexity ──────────────────────────────

SEARCH_GOAL_MODELS: dict[str, str] = {
    "scholarship": "sonar-pro",
    "funding":     "sonar-pro",
    "tender":      "sonar-pro",
    "career":      "sonar",
    "exam":        "sonar",
    "freelance":   "sonar",
    "study_grant": "sonar",
}

# ── Signaux de déclenchement ───────────────────────────────────────────────
# Score ≥ 2 pour déclencher (seuil abaissé — on préfère plus de fraîcheur).

_TEMPORAL = frozenset([
    # Avec accents
    "aujourd'hui", "cette semaine", "ce mois", "cette année",
    "récent", "récente", "récents", "récentes",
    "actuel", "actuelle", "actuels", "actuelles",
    "dernier", "dernière", "derniers", "dernières",
    "nouveau", "nouvelle", "nouveaux",
    "ouvert", "ouverte", "ouverts",
    "prochainement", "bientôt", "clôture",
    # Sans accents (saisie mobile courante)
    "recent", "recente", "recents",
    "actuel", "nouvelle",
    "ouvert", "cloture", "echeance",
    # Années et dates
    "2025", "2026", "2027",
    "deadline", "date limite", "quand est-ce", "quelles dates",
    "en cours", "disponible", "disponibles",
])  # poids 3

_SPECIFICITY = frozenset([
    # Demandes d'information précise
    "liste", "liste des", "liste de",
    "quelles bourses", "quel financement", "quels financements",
    "quels appels", "quelles offres", "quels concours",
    "quelles opportunités", "quelles opportunites",
    "où postuler", "comment candidater", "comment postuler",
    "quel site", "quelles sont les", "quels sont les",
    "trouver des", "trouve-moi", "trouve moi",
    "cherche", "recherche", "donne-moi", "donne moi",
    "montre-moi", "montre moi",
    "comment trouver", "où trouver", "ou trouver",
    "existe-t-il", "existe t-il", "y a-t-il", "y a t-il",
    "quels programmes", "quelles aides",
    # Mots-clés métier forts
    "bourse", "bourses", "financement", "financements",
    "subvention", "subventions", "appel d'offres", "appels d'offres",
    "appel doffres", "appels doffres",
    "offre d'emploi", "offres d'emploi",
    "concours", "recrutement",
])  # poids 2

_QUESTION_WORDS = frozenset([
    "quel", "quelle", "quels", "quelles",
    "comment", "pourquoi", "combien", "lequel", "laquelle",
    "où", "ou",
])  # poids 1 — question directe


def should_search(message: str, goal_type: str | None) -> tuple[bool, str]:
    """Détermine si une recherche Perplexity doit être déclenchée.

    Returns:
        (True, model_name) si score ≥ 2, (False, "") sinon.
    """
    if goal_type not in SEARCH_GOAL_MODELS:
        return False, ""

    lower = message.lower()
    score = 0

    for signal in _TEMPORAL:
        if signal in lower:
            score += 3
            break

    for signal in _SPECIFICITY:
        if signal in lower:
            score += 2
            break

    # Question directe (quel, comment, où…)
    words = lower.split()
    if words and words[0] in _QUESTION_WORDS:
        score += 1

    # Message détaillé → vraie demande de recherche
    if len(words) >= 6:
        score += 1

    if score < 2:
        return False, ""

    return True, SEARCH_GOAL_MODELS[goal_type]


# ── Construction de la query ───────────────────────────────────────────────

_GOAL_PREFIXES: dict[str, str] = {
    "scholarship": "bourses études {domain} {country} 2026 candidature ouverte",
    "funding":     "financements subventions {domain} {country} 2026 disponibles",
    "tender":      "appels offres {domain} {country} 2026 récents publiés",
    "career":      "offres emploi {domain} {country} 2026 recrutement",
    "exam":        "concours examens {domain} {country} 2026 dates inscriptions",
    "freelance":   "missions freelance {domain} {country} plateformes tarifs",
    "study_grant": "bourses recherche académique {domain} {country} 2026",
}


def build_search_query(message: str, goal_type: str | None, profile: dict) -> str:
    """Construit une query contextualisée pour Perplexity."""
    country = (profile.get("country") or "Afrique de l'Ouest").strip()
    domain  = (profile.get("domain") or profile.get("field_of_study") or "").strip()

    prefix_tpl = _GOAL_PREFIXES.get(goal_type or "", "{domain} {country}")
    prefix = " ".join(prefix_tpl.format(domain=domain, country=country).split())

    if prefix.lower() in message.lower():
        return message.strip()

    return f"{prefix} — {message.strip()}"
