"""Génération d'exercices QCM — appel LLM structuré, hors du framework SpecializedAgent.

Contrairement à create_course (classroom_course_service.py) qui dégrade silencieusement
vers une AgentResponse d'erreur si le LLM échoue, ici on ne peut pas dégrader : un
exercice à 0 question n'est pas un objet produit valide. Un échec de génération doit
remonter clairement au professeur (BadRequestError), pas produire un exercice vide.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from app.core.exceptions import BadRequestError
from app.llm import get_llm_provider
from app.models.structure import ClassroomExerciseKind
from app.schemas.exercise_generation import GeneratedExercise

logger = logging.getLogger(__name__)

# Mêmes regex que app.agents.base._strip_fences — dupliquées plutôt qu'importées :
# ce service ne doit pas dépendre des internals du framework agent conversationnel,
# qu'il contourne délibérément (cf. docstring app/schemas/exercise_generation.py).
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)
_FENCE_OPEN_RE = re.compile(r"^\s*```(?:json)?\s*\n?", re.DOTALL)


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return _FENCE_OPEN_RE.sub("", text).strip()


def _build_prompt(
    *, title: str, topic_hint: str, subject: str | None,
    kind: ClassroomExerciseKind, question_count: int, source_content: str | None,
) -> str:
    parts = [
        "Tu es un expert pédagogique. Génère un exercice à choix multiples (QCM) "
        f"intitulé « {title} »" + (f" (matière : {subject})" if subject else "") + ".",
        f"Consigne du professeur : {topic_hint}",
    ]
    if source_content:
        parts.append(f"Base-toi sur ce contenu de cours :\n{source_content[:4000]}")
    parts.append(f"Génère exactement {question_count} questions, chacune avec 3 ou 4 choix.")
    if kind == ClassroomExerciseKind.evaluation:
        parts.append(
            "C'est une évaluation notée : varie la difficulté et couvre des notions "
            "distinctes, pas seulement un seul angle du sujet."
        )
    parts.append(
        "Pour chaque question, assigne un topic_tag court (2-4 mots) identifiant la "
        "notion précise couverte (ex. \"dérivées composées\"), pour permettre plus "
        "tard de repérer les notions les moins maîtrisées par la classe."
    )
    parts.append(
        "Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ou après, "
        "sans balises markdown, respectant exactement ce schéma :\n"
        '{"title": "...", "instructions": "..." ou null, "questions": ['
        '{"prompt": "...", "choices": ["...", "..."], "correct_choice_index": 0, '
        '"explanation": "..." ou null, "topic_tag": "..." ou null}'
        "]}"
    )
    return "\n\n".join(parts)


def _parse_response(raw: str) -> GeneratedExercise:
    cleaned = _strip_fences(raw)
    data = json.loads(cleaned)
    return GeneratedExercise(**data)


def _drop_invalid_questions(exercise: GeneratedExercise) -> GeneratedExercise:
    """Retire les questions dont l'index de bonne réponse est hors bornes (erreur
    LLM ponctuelle) plutôt que de faire échouer toute la génération — sauf si ça
    ne laisserait plus aucune question valide."""
    valid = [q for q in exercise.questions if 0 <= q.correct_choice_index < len(q.choices)]
    if not valid:
        raise BadRequestError("La génération n'a produit aucune question exploitable. Réessaie.")
    if len(valid) < len(exercise.questions):
        logger.warning(
            "exercise_generation: %d question(s) invalide(s) retirée(s) sur %d",
            len(exercise.questions) - len(valid), len(exercise.questions),
        )
    return GeneratedExercise(title=exercise.title, instructions=exercise.instructions, questions=valid)


async def generate_exercise(
    *,
    title: str,
    topic_hint: str,
    subject: str | None,
    kind: ClassroomExerciseKind,
    question_count: int = 8,
    source_content: str | None = None,
) -> GeneratedExercise:
    prompt = _build_prompt(
        title=title, topic_hint=topic_hint, subject=subject,
        kind=kind, question_count=question_count, source_content=source_content,
    )
    messages = [
        {"role": "system", "content": "Tu es un assistant pédagogique expert. Réponds toujours en français."},
        {"role": "user", "content": prompt},
    ]
    llm = get_llm_provider()

    raw = await llm.complete(messages, max_tokens=4096, temperature=0.4)
    try:
        exercise = _parse_response(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("exercise_generation: JSON invalide au 1er essai (%s), retry", exc)
        messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Ta réponse n'était pas un JSON valide. Renvoie UNIQUEMENT le JSON, "
                    "sans aucun texte avant/après, en respectant strictement le schéma demandé."
                ),
            },
        ]
        raw = await llm.complete(messages, max_tokens=4096, temperature=0.4)
        try:
            exercise = _parse_response(raw)
        except (json.JSONDecodeError, ValidationError) as exc2:
            logger.error("exercise_generation: échec définitif après retry: %s", exc2)
            raise BadRequestError("La génération de l'exercice a échoué. Réessaie dans quelques instants.") from exc2

    return _drop_invalid_questions(exercise)
