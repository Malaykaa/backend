"""Génération d'exercices QCM — appel LLM structuré, hors du framework agent.

Verrouille : parsing JSON strict (avec fences markdown tolérées), un seul retry sur
JSON invalide puis échec propre, et le retrait (pas l'échec total) d'une question dont
l'index de bonne réponse est hors bornes.

Tests synchrones pilotant du code async via asyncio.run(), comme le reste de la
suite : chaque appel ouvre puis referme sa propre boucle, sans jamais dépendre
d'une boucle globale partagée entre tests.
"""

import asyncio
import json

import pytest

from app.core.exceptions import BadRequestError
from app.models.structure import ClassroomExerciseKind
from app.services import exercise_generation


def _run(coro):
    return asyncio.run(coro)


class _FakeLLM:
    """Retourne des réponses successives fixes, une par appel à complete()."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return self._responses.pop(0)


_VALID_JSON = json.dumps({
    "title": "Dérivées",
    "instructions": "Réponds à chaque question.",
    "questions": [
        {
            "prompt": "Dérivée de x^2 ?", "choices": ["x", "2x", "x^2", "2"],
            "correct_choice_index": 1, "explanation": "d/dx(x^2) = 2x", "topic_tag": "dérivées simples",
        },
        {
            "prompt": "Dérivée de sin(x) ?", "choices": ["cos(x)", "-cos(x)", "sin(x)", "-sin(x)"],
            "correct_choice_index": 0, "explanation": None, "topic_tag": "dérivées trigonométriques",
        },
    ],
})


def _install(monkeypatch, responses: list[str]) -> _FakeLLM:
    fake = _FakeLLM(responses)
    monkeypatch.setattr(exercise_generation, "get_llm_provider", lambda: fake)
    return fake


def _generate(**overrides):
    kwargs = {
        "title": "Dérivées", "topic_hint": "dérivées", "subject": None,
        "kind": ClassroomExerciseKind.exercise, "question_count": 2,
    }
    kwargs.update(overrides)
    return _run(exercise_generation.generate_exercise(**kwargs))


class TestParsingJSONValide:
    def test_json_valide_est_parse_correctement(self, monkeypatch):
        _install(monkeypatch, [_VALID_JSON])
        result = _generate(subject="Maths")
        assert result.title == "Dérivées"
        assert len(result.questions) == 2
        assert result.questions[0].correct_choice_index == 1

    def test_json_avec_fences_markdown_est_tolere(self, monkeypatch):
        _install(monkeypatch, [f"```json\n{_VALID_JSON}\n```"])
        result = _generate()
        assert len(result.questions) == 2


class TestRetrySurJSONInvalide:
    def test_json_invalide_puis_valide_reussit_au_retry(self, monkeypatch):
        fake = _install(monkeypatch, ["ceci n'est pas du JSON", _VALID_JSON])
        result = _generate()
        assert len(result.questions) == 2
        assert len(fake.calls) == 2  # un appel initial + un retry

    def test_json_invalide_deux_fois_leve_une_erreur_claire(self, monkeypatch):
        _install(monkeypatch, ["pas du json", "toujours pas du json"])
        with pytest.raises(BadRequestError):
            _generate()


class TestValidationSemantique:
    def test_question_avec_index_hors_bornes_est_retiree(self, monkeypatch):
        payload = json.loads(_VALID_JSON)
        payload["questions"][0]["correct_choice_index"] = 99  # hors bornes (4 choix)
        _install(monkeypatch, [json.dumps(payload)])

        result = _generate()
        assert len(result.questions) == 1
        assert result.questions[0].prompt == "Dérivée de sin(x) ?"

    def test_toutes_les_questions_invalides_leve_une_erreur(self, monkeypatch):
        payload = json.loads(_VALID_JSON)
        for q in payload["questions"]:
            q["correct_choice_index"] = 99
        _install(monkeypatch, [json.dumps(payload)])

        with pytest.raises(BadRequestError):
            _generate()
