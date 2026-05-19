"""Service objectifs — logique métier pour la gestion des goals."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.goal import Goal, GoalType
from app.repositories.chat_repo import ChatRepository
from app.repositories.goal_repo import GoalRepository

# Questions de diagnostic par type d'objectif
_DIAGNOSTIC_QUESTIONS: dict[str, list[str]] = {
    "exam": [
        "Quel examen prépares-tu ?",
        "Dans combien de temps a lieu l'examen ?",
        "Quelles matières ou chapitres te posent problème ?",
        "Quel est ton niveau actuel (notes récentes) ?",
        "Combien d'heures par jour peux-tu consacrer aux révisions ?",
    ],
    "scholarship": [
        "Quel type de bourse ou stage recherches-tu ?",
        "Quel est ton niveau d'études actuel ?",
        "Dans quel domaine ou filière ?",
        "Quel pays ou région cible ?",
        "As-tu déjà identifié des programmes spécifiques ?",
    ],
    "funding": [
        "Quel est ton projet en quelques mots ?",
        "Quel montant recherches-tu approximativement ?",
        "As-tu déjà un business plan ?",
        "Où en es-tu dans le projet (idée, prototype, lancement) ?",
        "Dans quel pays / secteur opères-tu ?",
    ],
    "tender": [
        "De quel appel d'offres s'agit-il ?",
        "Quelle est la date limite de soumission ?",
        "Quel est ton domaine d'expertise ?",
        "As-tu déjà répondu à des appels d'offres ?",
        "As-tu accès au cahier des charges ?",
    ],
    "study_grant": [
        "Quel niveau d'études vises-tu (licence, master, doctorat) ?",
        "Dans quel domaine ou filière ?",
        "Quel(s) pays cibles-tu ?",
        "Quel est ton parcours académique actuel ?",
        "As-tu des contraintes de budget ou de calendrier ?",
    ],
    "career": [
        "Quel est ton métier ou poste actuel ?",
        "Vers quel domaine souhaites-tu te reconvertir ?",
        "Quelles sont tes compétences principales ?",
        "As-tu des contraintes (financières, géographiques) ?",
        "Quel est ton niveau de formation actuel ?",
    ],
}


_GOAL_TYPE_LABELS: dict[str, str] = {
    "exam": "Préparation examen",
    "scholarship": "Recherche bourse / stage",
    "funding": "Recherche financement",
    "tender": "Réponse appel d'offres",
    "study_grant": "Bourse d'étude",
    "career": "Carrière & emploi",
}


class GoalService:
    def __init__(self, db: Session) -> None:
        self.repo = GoalRepository(db)
        self.chat_repo = ChatRepository(db)

    def create_goal(
        self,
        user_id: uuid.UUID,
        goal_type: GoalType,
        context_data: dict | None = None,
        *,
        title: str | None = None,
    ) -> Goal:
        goal = self.repo.create_goal(
            user_id=user_id,
            goal_type=goal_type,
            context_data=context_data,
        )

        # Créer un thread de chat lié à l'objectif pour la continuité conversationnelle
        thread_title = title or _GOAL_TYPE_LABELS.get(goal_type.value, "Objectif")
        self.chat_repo.create_thread(
            user_id=user_id,
            title=thread_title,
            goal_id=goal.id,
        )

        return goal

    def get_goals(self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[Goal]:
        return self.repo.get_user_goals(user_id, limit=limit, offset=offset)

    def get_goal(self, goal_id: uuid.UUID, user_id: uuid.UUID) -> Goal:
        goal = self.repo.get_with_plan(goal_id)
        if not goal or goal.user_id != user_id:
            raise NotFoundError("Objectif")
        return goal

    @staticmethod
    def get_diagnostic_questions(goal_type: str) -> list[str]:
        return _DIAGNOSTIC_QUESTIONS.get(goal_type, [])
