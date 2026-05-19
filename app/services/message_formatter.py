"""Helpers de formatage des messages pour le frontend.

Centralise la transformation contenu LLM → format affichable :
- injection de marqueurs structurés (@@STEPS@@, @@PROPOSITIONS@@)
- déduplication suggestions/questions présentes à la fois en Markdown et en méta
- formatage des sources

Aucune dépendance ORM : opère sur des dict/str pour rester réutilisable
depuis n'importe quel router (chat, action, etc.).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import uuid

_SUGGESTION_TRIGGERS = (
    "si tu veux",
    "si tu le souhaites",
    "tu veux que je",
    "je peux",
    "je te propose de",
)

_BULLET_CHARS = frozenset("-•")

# Supprime le bloc Sources UNIQUEMENT s'il est en queue absolue du texte.
#
# Règle : le bloc Sources ne peut être suivi que de ses propres items de liste
# (lignes commençant par - ou *) puis de la fin de chaîne.
# Si du contenu significatif suit (clarifications, @@STEPS@@, @@PROPOSITIONS@@…),
# le bloc n'est PAS supprimé — évite de couper les questions et boutons d'action.
#
# Variantes du header couvertes :
#   **Sources :**  /  ## Sources  /  Sources:  /  ---\n**Sources :**
_SOURCES_BLOCK_RE = re.compile(
    r"\n{1,3}"
    r"(?:---[ \t]*\n)?"                          # séparateur horizontal optionnel
    r"(?:#{1,3}[ \t]+)?"                         # titre markdown (## Sources)
    r"\*{0,2}[ \t]*[Ss]ources?[ \t]*:?[ \t]*\*{0,2}"  # "Sources", "**Sources :**"
    r"[^\n]*"                                    # reste de la ligne du header
    r"(?:\n[ \t]*[-*][ \t][^\n]*)?"             # items de liste optionnels
    r"(?:\n[ \t]*[-*][ \t][^\n]*)*"             # autres items de liste
    r"\s*$",                                     # fin de chaîne (rien d'autre après)
    re.DOTALL,
)


def strip_sources_block(content: str) -> str:
    """Supprime le bloc Sources en queue de texte (toutes variantes LLM).

    Ne supprime rien si du contenu suit le bloc Sources
    (questions de clarification, marqueurs @@STEPS@@, etc.).
    """
    return _SOURCES_BLOCK_RE.sub("", content).rstrip()


def strip_trailing_suggestions(content: str) -> str:
    """Supprime les blocs de suggestions en fin de texte (doublon avec @@PROPOSITIONS@@).

    Parsing ligne par ligne (pas de regex) pour éviter le backtracking catastrophique.
    """
    lines = content.split("\n")
    i = len(lines) - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    while i >= 0 and lines[i].strip() and lines[i].strip()[0] not in _BULLET_CHARS:
        i -= 1
    bullet_end = i
    while i >= 0 and lines[i].strip() and lines[i].strip()[0] in _BULLET_CHARS:
        i -= 1
    bullet_start = i + 1
    if bullet_start > bullet_end:
        return content
    trigger_idx = bullet_start - 1
    while trigger_idx >= 0 and not lines[trigger_idx].strip():
        trigger_idx -= 1
    if trigger_idx < 0:
        return content
    trigger_line = lines[trigger_idx].strip().lower()
    if not any(trigger_line.startswith(t) for t in _SUGGESTION_TRIGGERS):
        return content
    return "\n".join(lines[:trigger_idx]).rstrip()


def inject_markers(content: str, payload: dict | None) -> str:
    """Injecte les marqueurs structurés dans le contenu Markdown.

    - clarifications → liste numérotée en texte (toujours visibles)
    - steps → @@STEPS@@ (boutons cliquables côté frontend)
    - suggestions → @@PROPOSITIONS@@ (boutons cliquables côté frontend)

    Dé-duplique avant injection pour éviter que le LLM répète en Markdown
    ce que les champs structurés portent déjà.
    """
    # Nettoyage défensif : supprimer tout bloc Sources présent dans le texte,
    # qu'il vienne du LLM ou d'anciens messages sauvegardés en DB.
    content = strip_sources_block(content)

    if not payload:
        return content

    clarifications = payload.get("clarifications", [])
    suggestions = payload.get("suggestions", [])
    steps = payload.get("steps", [])

    # Le prompt LLM (app/agents/base.py) interdit désormais aux agents
    # de répéter les questions en Markdown ; on ne dé-duplique plus que
    # les blocs de suggestions, plus difficiles à contraindre côté prompt.
    if suggestions:
        content = strip_trailing_suggestions(content)

    if clarifications:
        questions = "\n".join(
            f"{i+1}. {q}"
            for i, q in enumerate(clarifications)
            if isinstance(q, str) and q.strip()
        )
        if questions:
            content += f"\n\n{questions}"

    if steps:
        formatted = [
            {
                "id": s.get("id", str(i)),
                "title": s.get("label", ""),
                "type": "step",
                "description": s.get("description", ""),
            }
            for i, s in enumerate(steps)
        ]
        encoded = urllib.parse.quote(json.dumps(formatted, ensure_ascii=False))
        content += f"\n\n@@STEPS@@ {encoded}"

    if suggestions:
        labels = [s["label"] if isinstance(s, dict) else str(s) for s in suggestions]
        encoded = urllib.parse.quote(json.dumps(labels, ensure_ascii=False))
        content += f"\n\n@@PROPOSITIONS@@ {encoded}"

    return content


def format_sources(payload: dict | None) -> list[dict] | None:
    """Extrait et formate les sources depuis le payload agent."""
    if not payload:
        return None
    sources = payload.get("sources", [])
    if not sources:
        return None
    return [
        {
            "documentId": s.get("url", str(uuid.uuid4())),
            "score": 85,
            "title": s.get("title", ""),
            "url": s.get("url", ""),
        }
        for s in sources
        if isinstance(s, dict)
    ]
