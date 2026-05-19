"""Service plans — génération de plan via l'orchestrateur et gestion des étapes."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.agents.base import AgentContext, AgentResponse
from app.agents.orchestrator import Orchestrator
from app.core.exceptions import BadRequestError, NotFoundError
from app.llm import get_llm_provider
from app.models.goal import Goal
from app.models.plan import Plan, PlanStep
from app.repositories.chat_repo import ChatRepository
from app.repositories.goal_repo import GoalRepository
from app.repositories.plan_repo import PlanRepository


class PlanService:
    def __init__(self, db: Session) -> None:
        self.plan_repo = PlanRepository(db)
        self.goal_repo = GoalRepository(db)

    def get_plan(self, goal_id: uuid.UUID, user_id: uuid.UUID) -> Plan:
        goal = self.goal_repo.get_by_id(goal_id)
        if not goal or goal.user_id != user_id:
            raise NotFoundError("Objectif")

        plan = self.plan_repo.get_by_goal_id(goal_id)
        if not plan:
            raise NotFoundError("Plan")
        return plan

    async def generate_plan(
        self,
        goal_id: uuid.UUID,
        user_id: uuid.UUID,
        answers: dict,
        profile: dict | None = None,
    ) -> Plan:
        """Génère un plan en appelant l'orchestrateur avec les réponses du diagnostic."""
        goal = self.goal_repo.get_by_id(goal_id)
        if not goal or goal.user_id != user_id:
            raise NotFoundError("Objectif")

        # Vérifier qu'un plan n'existe pas déjà
        existing = self.plan_repo.get_by_goal_id(goal_id)
        if existing:
            raise BadRequestError("Un plan existe déjà pour cet objectif.")

        # Sauvegarder les réponses dans le context_data du goal
        goal.context_data = {**(goal.context_data or {}), "diagnostic_answers": answers}
        self.goal_repo.save(goal)

        # Construire le message pour l'orchestrateur
        message = self._build_message(goal.type.value, answers)

        ctx = AgentContext(
            user_id=user_id,
            message=message,
            profile=profile or {},
            goal_type=goal.type.value,
            goal_context=answers,
        )

        # Appeler l'orchestrateur
        llm = get_llm_provider()
        orchestrator = Orchestrator(llm)
        try:
            agent_response = await orchestrator.route(ctx)
        except Exception as exc:
            logger.error("Orchestration failed for plan generation (goal %s): %s", goal_id, exc)
            agent_response = AgentResponse(
                explanation="Désolé, je n'ai pas pu générer ton plan. Réessaie dans quelques instants.",
                agent_id="error",
            )

        # Créer le plan en DB
        plan = self.plan_repo.create_plan(
            goal_id=goal_id,
            summary=agent_response.explanation[:300],
            explanation=agent_response.explanation,
            sources=[s.model_dump() for s in agent_response.sources] if agent_response.sources else None,
            suggestions=[s.model_dump() for s in agent_response.suggestions] if agent_response.suggestions else None,
        )

        # Créer les étapes
        for step in agent_response.steps:
            self.plan_repo.add_step(
                plan_id=plan.id,
                label=step.label,
                description=step.description,
                order=step.order,
            )

        return self.plan_repo.get_with_steps(plan.id)

    def maybe_persist_from_response(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        agent_response: AgentResponse,
    ) -> None:
        """Persiste le plan si l'agent retourne des steps et que le goal n'a pas encore de plan.

        Idempotent : ne fait rien si pas de goal lié au thread, ou si un plan existe déjà.
        Appelé après handle_message()/stream_message() avant le commit.
        """
        if not agent_response.steps:
            return

        thread = ChatRepository(self.plan_repo.db).get_thread_with_messages(thread_id)
        if not thread or thread.user_id != user_id or not thread.goal:
            return

        if self.plan_repo.get_by_goal_id(thread.goal_id):
            return  # Plan existe déjà

        try:
            plan = self.plan_repo.create_plan(
                goal_id=thread.goal_id,
                summary=agent_response.explanation[:300],
                explanation=agent_response.explanation,
                sources=[s.model_dump() for s in agent_response.sources] if agent_response.sources else None,
                suggestions=[s.model_dump() for s in agent_response.suggestions] if agent_response.suggestions else None,
            )
            for step in agent_response.steps:
                self.plan_repo.add_step(
                    plan_id=plan.id,
                    label=step.label,
                    description=step.description,
                    order=step.order,
                )
            logger.info(
                "Auto-persisted plan for goal %s (%d steps)",
                thread.goal_id, len(agent_response.steps),
            )
        except Exception:
            logger.exception("Failed to auto-persist plan for goal %s", thread.goal_id)

    def complete_step(
        self,
        goal_id: uuid.UUID,
        step_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> PlanStep:
        """Marque une étape comme terminée."""
        goal = self.goal_repo.get_by_id(goal_id)
        if not goal or goal.user_id != user_id:
            raise NotFoundError("Objectif")

        plan = self.plan_repo.get_by_goal_id(goal_id)
        if not plan:
            raise NotFoundError("Plan")

        step = self.plan_repo.get_step(step_id)
        if not step or step.plan_id != plan.id:
            raise NotFoundError("Étape")

        return self.plan_repo.complete_step(step)

    @staticmethod
    def _build_message(goal_type: str, answers: dict) -> str:
        """Construit un message résumant les réponses du diagnostic."""
        parts = [f"Je souhaite de l'aide pour mon objectif de type '{goal_type}'."]
        for question, answer in answers.items():
            parts.append(f"- {question} : {answer}")
        return "\n".join(parts)
