"""Base des agents — Protocol, SpecializedAgent, dataclasses AgentContext & AgentResponse."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from app.agents.guardrails import GUARDRAILS_SHORT, MALAYKAA_IDENTITY
from app.llm.base import LLMProvider
from app.llm.continuation import complete_with_continuation

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)
_FENCE_OPEN_RE = re.compile(r"^\s*```(?:json)?\s*\n?", re.DOTALL)
_MD_LINK_RE = re.compile(r"\[([^\]]{2,})\]\((https?://[^\s)]+)\)")
_META_BLOCK_RE = re.compile(r"@@META@@\s*\n(.*?)@@END@@", re.DOTALL)

# Supprime le bloc Sources UNIQUEMENT s'il est en queue absolue du texte LLM.
# Même logique que _SOURCES_BLOCK_RE dans message_formatter.py — dupliqué ici
# pour rester indépendant du service (base.py ne doit pas importer les services).
_SOURCES_TAIL_RE = re.compile(
    r"\n{1,3}"
    r"(?:---[ \t]*\n)?"
    r"(?:#{1,3}[ \t]+)?"
    r"\*{0,2}[ \t]*[Ss]ources?[ \t]*:?[ \t]*\*{0,2}"
    r"[^\n]*"                                # reste de la ligne du header
    r"(?:\n[ \t]*[-*][ \t][^\n]*)?"         # items de liste optionnels
    r"(?:\n[ \t]*[-*][ \t][^\n]*)*"         # autres items de liste
    r"\s*$",                                 # fin de chaîne obligatoire
    re.DOTALL,
)

# ── Champs de profil critiques par goal_type (A5) ────────────────────────────
#
# Quand ces champs sont absents du profil, on injecte un hint dans _build_messages()
# pour que l'agent les priorise dans son diagnostic ou les extraie de l'historique.
# Format : (clé_profil, libellé_lisible_pour_le_LLM)

_CRITICAL_FIELDS: dict[str, list[tuple[str, str]]] = {
    "scholarship": [
        ("country",  "pays ou région cible"),
        ("domain",   "domaine d'études ou secteur"),
        ("level",    "niveau d'études visé (licence/master/doctorat)"),
    ],
    "study_grant": [
        ("country",  "pays de destination souhaité"),
        ("domain",   "domaine ou filière d'études"),
        ("level",    "niveau d'études visé"),
    ],
    "funding": [
        ("country",  "pays ou région d'opération du projet"),
        ("domain",   "secteur ou domaine du projet"),
    ],
    "career": [
        ("country",  "localisation ou zone géographique souhaitée"),
        ("domain",   "domaine professionnel ou secteur cible"),
    ],
    "exam": [
        ("country",  "pays du concours ou de l'examen"),
        ("domain",   "matière principale ou filière préparée"),
    ],
    "tender": [
        ("country",  "zone géographique du marché ciblé"),
        ("domain",   "secteur d'activité de l'entreprise"),
    ],
    "freelance": [
        ("domain",   "compétence principale ou métier freelance"),
    ],
}


def _get_missing_critical_fields(
    profile: dict, goal_type: str | None
) -> list[tuple[str, str]]:
    """Retourne les champs critiques absents du profil pour ce goal_type.

    Règle domaine : "domain" est satisfait par profile["domain"] OU
    profile["field_of_study"] — les étudiants n'ont pas de "domain" mais
    renseignent leur filière via "field_of_study" à l'inscription.
    """
    if not goal_type:
        return []
    missing = []
    for key, label in _CRITICAL_FIELDS.get(goal_type, []):
        if key == "domain":
            if not profile.get("domain") and not profile.get("field_of_study"):
                missing.append((key, label))
        else:
            if not profile.get(key):
                missing.append((key, label))
    return missing


# Instruction de format de réponse — Markdown natif avec métadonnées légères en fin.
# Chaque agent spécialisé fournit SYSTEM_PROMPT (le « quoi »),
# _RESPONSE_FORMAT_INSTRUCTION ajoute le « comment ».
_RESPONSE_FORMAT_INSTRUCTION = """\
## Format de réponse
Réponds en **Markdown naturel** (titres, listes, gras, liens). C'est ton contenu principal.

### Bloc @@META@@ (optionnel, en toute fin de réponse)
Tu peux ajouter des données structurées **après** ton contenu Markdown.
ATTENTION : les séparateurs sont stricts. Utilise | entre les items, :: entre titre et description d'une étape.

Exemple diagnostic (tu as besoin d'infos) :
@@META@@
clarifications: Quel examen prépares-tu ? | Dans combien de temps ? | Quelles matières te posent problème ?
suggestions: Voir les concours disponibles | Explorer les ressources de révision
@@END@@

Exemple plan (tu as assez d'infos) :
@@META@@
steps: Consolider les bases :: Revoir algèbre et analyse de terminale | S'entraîner :: Faire des exercices types et annales | Simuler :: Examens blancs chronométrés
suggestions: Créer un planning de révision | Trouver des annales corrigées
@@END@@

### Règles STRICTES
- **clarifications et steps sont MUTUELLEMENT EXCLUSIFS** : si tu poses des questions → clarifications, PAS de steps. Si tu donnes un plan → steps, PAS de clarifications.
- **Question explicative** (comment, pourquoi, explique) : réponds en Markdown. PAS de steps ni clarifications.
- **Diagnostic** (pas assez d'infos) : place tes questions UNIQUEMENT dans le bloc `clarifications` (le frontend les affichera comme liste numérotée). Le Markdown sert à introduire ou contextualiser, mais ne répète PAS les questions sous forme de liste numérotée — sinon l'utilisateur les voit en double. JAMAIS de steps.
- **Plan** (assez d'infos) : détaille en Markdown ET ajoute 3-7 étapes dans steps. JAMAIS de clarifications.
- **Sources** : n'ajoute PAS de bloc "Sources" ni de liens externes dans ta réponse. Réponds uniquement à partir de ta connaissance.
- **Ne répète PAS** des questions déjà posées si l'utilisateur y a répondu.
- Le bloc @@META@@ est OPTIONNEL. Omets-le si tu n'as rien à structurer.
- Langue : français."""


def _strip_fences(text: str) -> str:
    """Retire les balises markdown ``` autour du JSON si présentes.

    Gère aussi le cas où la réponse est tronquée (fence ouvrante sans fermante).
    """
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # Pas de fence fermante (réponse tronquée) → retirer juste l'ouvrante
    stripped = _FENCE_OPEN_RE.sub("", text).strip()
    return stripped


def _normalize_agent_data(data: dict) -> None:
    """Corrige les types renvoyés par le LLM pour coller au schéma AgentResponse.

    Gemini renvoie parfois une string là où on attend une liste,
    ou des strings dans une liste qui attend des objets Pydantic.
    """
    # Champs qui attendent list[str] : garder les strings, virer le reste
    for key in ("clarifications", "deliverables"):
        val = data.get(key)
        if isinstance(val, str):
            data[key] = [val] if val else []
        elif isinstance(val, list):
            data[key] = [v for v in val if isinstance(v, str)]
        else:
            data[key] = []

    # suggestions : list[Suggestion] — chaque item doit avoir id + label
    raw_sugg = data.get("suggestions")
    if not isinstance(raw_sugg, list):
        data["suggestions"] = []
    else:
        normalized = []
        for i, s in enumerate(raw_sugg):
            if isinstance(s, dict) and "label" in s:
                s.setdefault("id", str(i + 1))
                normalized.append(s)
            elif isinstance(s, str) and s:
                normalized.append({"id": str(i + 1), "label": s})
        data["suggestions"] = normalized

    # steps : list[Step] — chaque item doit avoir id, label, description, order
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        data["steps"] = []
    else:
        normalized = []
        for i, s in enumerate(raw_steps):
            if isinstance(s, dict):
                s.setdefault("id", str(i + 1))
                s.setdefault("label", s.get("task", s.get("title", f"Étape {i+1}")))
                s.setdefault("description", s.get("detail", ""))
                s.setdefault("order", i + 1)
                normalized.append(s)
        data["steps"] = normalized

    # sources : list[Source] — chaque item doit avoir title + url
    raw_src = data.get("sources")
    if not isinstance(raw_src, list):
        data["sources"] = []
    else:
        data["sources"] = [s for s in raw_src if isinstance(s, dict) and "title" in s and "url" in s]


def _extract_markdown_sources(text: str) -> list[dict]:
    """Extrait les liens markdown [Titre](https://url) du texte comme objets Source.

    Déduplique par URL et ne conserve que les liens HTTP(S).
    """
    seen_urls: set[str] = set()
    sources: list[dict] = []
    for match in _MD_LINK_RE.finditer(text):
        title = match.group(1).strip()
        url = match.group(2).strip()
        if url not in seen_urls and title:
            seen_urls.add(url)
            sources.append({"title": title, "url": url})
    return sources


def _parse_meta_block(raw: str) -> tuple[str, dict]:
    """Sépare le contenu Markdown du bloc @@META@@ optionnel.

    Retourne (markdown_content, meta_dict).
    meta_dict peut contenir : clarifications, steps, suggestions, sources.
    """
    meta: dict = {}
    m = _META_BLOCK_RE.search(raw)
    if not m:
        # Pas de bloc @@META@@ → tout est du Markdown
        # Nettoyer un éventuel @@META@@ orphelin (sans @@END@@, réponse tronquée)
        content = re.sub(r"@@META@@.*", "", raw, flags=re.DOTALL).strip()
        return content, meta

    # Séparer le Markdown du bloc méta
    content = raw[: m.start()].strip()
    block = m.group(1)

    for line in block.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue

        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if not value:
            continue

        if key == "clarifications":
            meta["clarifications"] = [q.strip() for q in value.split("|") if q.strip()]

        elif key == "steps":
            steps = []
            for i, raw_step in enumerate(value.split("|")):
                raw_step = raw_step.strip()
                if not raw_step:
                    continue
                if "::" in raw_step:
                    label, _, desc = raw_step.partition("::")
                    steps.append({"id": str(i + 1), "label": label.strip(), "description": desc.strip(), "order": i + 1})
                else:
                    steps.append({"id": str(i + 1), "label": raw_step, "description": "", "order": i + 1})
            meta["steps"] = steps

        elif key == "suggestions":
            meta["suggestions"] = [
                {"id": str(i + 1), "label": s.strip()}
                for i, s in enumerate(value.split("|"))
                if s.strip()
            ]

        elif key == "sources":
            sources = []
            for raw_src in value.split("|"):
                raw_src = raw_src.strip()
                if ">>" in raw_src:
                    title, _, url = raw_src.partition(">>")
                    title, url = title.strip(), url.strip()
                    if title and url:
                        sources.append({"title": title, "url": url})
            meta["sources"] = sources

    return content, meta


# ── Schémas de réponse agent (contrat unique) ─────────────


class Step(BaseModel):
    id: str
    label: str
    description: str
    order: int


class Suggestion(BaseModel):
    id: str
    label: str  # format : "Si tu veux je peux te : ..."


class Source(BaseModel):
    title: str
    url: str


class AgentResponse(BaseModel):
    """Format unifié de toute réponse IA dans Malayka."""

    explanation: str
    clarifications: list[str] = []
    steps: list[Step] = []
    suggestions: list[Suggestion] = []
    sources: list[Source] = []
    deliverables: list[str] = []
    agent_id: str


# ── Contexte transmis à chaque agent ─────────────────────


class AgentContext(BaseModel):
    """Données nécessaires à un agent pour produire sa réponse."""

    user_id: uuid.UUID
    message: str
    history: list[dict] = []
    profile: dict = {}
    goal_type: str | None = None
    goal_type_source: Literal["goal", "inferred"] | None = None
    goal_context: dict = {}
    # Images jointes au message — format Claude natif : {"media_type": "image/jpeg", "data": "<base64>"}
    image_data: list[dict] = []


# ── Protocol que chaque agent doit implémenter ───────────


@runtime_checkable
class AgentBase(Protocol):
    """Interface commune pour tous les agents Malayka."""

    AGENT_ID: str

    async def process(self, ctx: AgentContext) -> AgentResponse: ...


# ── Classe de base pour les agents spécialisés ───────────


class SpecializedAgent:
    """Base concrète pour tous les agents spécialisés.

    Chaque sous-classe définit AGENT_ID et SYSTEM_PROMPT.
    La logique process() → _build_messages() → LLM → _parse() est commune.
    Les sous-classes peuvent override process() ou _parse() si besoin (ex: DocumentAgent).
    """

    AGENT_ID: str = ""
    SYSTEM_PROMPT: str = ""

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def process(self, ctx: AgentContext) -> AgentResponse:
        """Construit les messages, appelle le LLM, parse la réponse."""
        messages = self._build_messages(ctx)
        raw = await complete_with_continuation(self.llm, messages)
        response = self._parse(raw)
        return self._inject_sources(response, ctx)

    def _build_messages(self, ctx: AgentContext) -> list[dict]:
        """Construit la liste de messages pour le LLM.

        Ordre : system prompt → response format → profil → goal_context → historique → message user.
        """
        messages: list[dict] = [
            {"role": "system", "content": MALAYKAA_IDENTITY},
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "system", "content": GUARDRAILS_SHORT},
            {"role": "system", "content": _RESPONSE_FORMAT_INSTRUCTION},
        ]

        if ctx.profile:
            profile_info = ", ".join(f"{k}: {v}" for k, v in ctx.profile.items() if v)
            if profile_info:
                messages.append({
                    "role": "system",
                    "content": f"Profil de l'utilisateur : {profile_info}",
                })

        # Hint A5 : champs critiques manquants → l'agent les priorise
        missing = _get_missing_critical_fields(ctx.profile, ctx.goal_type)
        if missing:
            labels = ", ".join(f"« {label} »" for _, label in missing)
            messages.append({
                "role": "system",
                "content": (
                    f"Champs de profil non encore renseignés : {labels}.\n"
                    "• Si l'utilisateur les a déjà mentionnés dans ce message ou "
                    "l'historique, extrais l'information et réponds directement "
                    "sans les redemander.\n"
                    "• S'ils restent inconnus, intègre-les en priorité dans tes "
                    "questions diagnostiques (max 2-3 questions en une fois)."
                ),
            })

        # goal_context : exclure les clés traitées séparément pour éviter les doublons.
        # - relevant_offers : affichées via POUR TOI, pas dans les réponses agents
        # - search_results  : traitées dans le bloc dédié ci-dessous
        _CTX_EXCLUDED = {"relevant_offers", "search_results"}
        goal_ctx_clean = {
            k: v for k, v in ctx.goal_context.items()
            if k not in _CTX_EXCLUDED
        } if ctx.goal_context else {}

        if goal_ctx_clean:
            messages.append({
                "role": "system",
                "content": f"Contexte de l'objectif : {json.dumps(goal_ctx_clean, ensure_ascii=False)}",
            })

        # Note : l'injection de search_results (Perplexity) est désactivée.
        # Les données web brutes injectées entraînaient la génération systématique
        # de blocs "Sources" avec des liens hors-sujet (programmes d'échange, etc.)
        # dans toutes les réponses, y compris les messages d'accueil.

        for h in ctx.history[-20:]:
            messages.append({"role": h["role"], "content": h["content"]})

        # Message utilisateur — multimodal si des images sont jointes
        if ctx.image_data:
            content_blocks: list[dict] = []
            for img in ctx.image_data:
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data"],
                    },
                })
            content_blocks.append({"type": "text", "text": ctx.message})
            messages.append({"role": "user", "content": content_blocks})
        else:
            messages.append({"role": "user", "content": ctx.message})
        return messages

    def _parse(self, raw: str) -> AgentResponse:
        """Parse la réponse du LLM en AgentResponse.

        Mode principal : Markdown + bloc @@META@@ optionnel.
        Fallback JSON : rétro-compat MockProvider et anciens prompts.
        """
        # 1. Essayer JSON d'abord (rétro-compat MockProvider)
        try:
            cleaned = _strip_fences(raw)
            data = json.loads(cleaned)
            if isinstance(data, dict) and "explanation" in data:
                data["agent_id"] = self.AGENT_ID
                # Nettoyer les sources dans l'explanation JSON aussi
                if isinstance(data.get("explanation"), str):
                    data["explanation"] = _SOURCES_TAIL_RE.sub("", data["explanation"]).rstrip()
                data["sources"] = []  # sources désactivées
                _normalize_agent_data(data)
                return AgentResponse(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # 2. Mode principal : Markdown + @@META@@ optionnel
        content, meta = _parse_meta_block(raw)

        # Nettoyage défensif : supprimer tout bloc "Sources" que le LLM aurait
        # généré malgré l'instruction (ex: "**Sources :**\n- [lien](url)").
        content = _SOURCES_TAIL_RE.sub("", content).rstrip()

        return AgentResponse(
            explanation=content,
            clarifications=meta.get("clarifications", []),
            steps=[Step(**s) for s in meta.get("steps", [])],
            suggestions=[Suggestion(**s) for s in meta.get("suggestions", [])],
            sources=[],   # sources désactivées — plus de liens hors-sujet
            agent_id=self.AGENT_ID,
        )

    def _inject_sources(self, response: AgentResponse, ctx: AgentContext) -> AgentResponse:
        """Sources injection désactivée — les sources Perplexity ne sont plus forcées
        dans les réponses pour éviter les liens hors-sujet (ex: programmes d'échange)."""
        return response
