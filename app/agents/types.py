"""Types énumérés partagés entre agents, orchestrateur et routers.

StrEnum garantit que chaque valeur EST une str Python — pas de coercition
nécessaire dans les comparaisons ou les modèles Pydantic existants.
L'autocomplete IDE prévient les fautes de frappe ; la mise à jour d'un
nom de valeur se propage partout sans cherche-et-remplace manuel.
"""

from enum import StrEnum


class GoalType(StrEnum):
    """Identifiant de l'agent / type d'objectif utilisateur."""

    EXAM        = "exam"
    SCHOLARSHIP = "scholarship"
    FUNDING     = "funding"
    FREELANCE   = "freelance"
    TENDER      = "tender"
    STUDY_GRANT = "study_grant"
    CAREER      = "career"
    DOCUMENT    = "document"
    FREE        = "free"
    WORKFLOW    = "workflow"  # agent_id synthétique pour les réponses multi-étapes


class AgentMode(StrEnum):
    """Mode d'exécution renvoyé par le Triage."""

    DIRECT   = "direct"
    WORKFLOW = "workflow"
    GENERATE = "generate"  # génération de document immédiate (bypass conversationnel)
