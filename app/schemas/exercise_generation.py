"""Schéma de sortie structurée pour la génération d'exercices QCM.

Distinct des schémas conversationnels (AgentResponse/@@META@@, cf. app.agents.base) :
la génération d'exercice est un appel LLM ponctuel qui doit renvoyer du JSON pur, pas
du Markdown avec un bloc méta plat — la structure imbriquée (questions -> choix ->
index correct) ne rentre pas dans ce DSL. Cf. le précédent ai_assist_section dans
structure_router.py pour le même genre d'appel direct, hors du framework agent.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class GeneratedQuestion(BaseModel):
    prompt: str
    choices: list[str]
    correct_choice_index: int
    explanation: str | None = None
    topic_tag: str | None = None

    @field_validator("choices")
    @classmethod
    def _validate_choices(cls, v: list[str]) -> list[str]:
        if not (2 <= len(v) <= 6):
            raise ValueError("Une question doit avoir entre 2 et 6 choix.")
        return v


class GeneratedExercise(BaseModel):
    title: str
    instructions: str | None = None
    questions: list[GeneratedQuestion]

    @field_validator("questions")
    @classmethod
    def _validate_questions(cls, v: list["GeneratedQuestion"]) -> list["GeneratedQuestion"]:
        if not v:
            raise ValueError("L'exercice doit contenir au moins une question.")
        return v
