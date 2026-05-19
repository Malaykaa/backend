"""Agent Document — génération de documents en Markdown pur.

Deux modes d'utilisation :
- process()  : appel unique, retourne le document complet (mode non-streaming)
- Le mode section-par-section est géré par ExecutionEngine._execute_document()
  qui utilise les configs de deliverable_configs.py et yield des ProgressEvent.

Le LLM retourne du Markdown directement — pas de JSON wrapping.
Les métadonnées (suggestions, steps, etc.) sont construites côté Python.

Détection de follow-up (A4) :
    _is_follow_up() est désormais async et utilise une classification LLM légère
    (temperature=0, max_tokens=5) pour distinguer "modification du document existant"
    de "nouvelle demande de document". Coût : ~50 tokens / ~0.3s.
    Appelée uniquement si l'historique contient déjà une réponse assistant.
"""

from __future__ import annotations

import logging

from app.agents.base import AgentContext, AgentResponse, Suggestion, SpecializedAgent
from app.agents.deliverable_configs import (
    DELIVERABLE_CONFIGS,
    DEFAULT_CONFIG,
    detect_doc_type,
)
from app.llm.base import LLMProvider

# Signaux indiquant une demande explicite de document formaté.
# Utilisé par _is_follow_up() et importé par l'orchestrateur (DRY).
DOCUMENT_FORMAT_SIGNALS = (
    "dans un document", "en document",
    "en pdf", "le pdf", "un pdf",
    "mets en forme", "met en forme", "mise en forme",
    "fais-moi un document", "fais moi un document",
    "fais un document", "crée un document", "cree un document",
    "génère un document", "genere un document",
    "rédige le document", "redige le document",
    "prépare un document", "prepare un document",
    "envoie le doc", "envoie le document",
)

logger = logging.getLogger(__name__)


def _needs_clarification(message: str) -> str | None:
    """Vérifie si le message est suffisamment précis pour générer un document.

    Retourne la question de clarification ou None si le message est OK.
    Inspiré de l'ancien backend (needsClarification).
    """
    text = message.strip()

    if len(text) == 0:
        return (
            "Peux-tu me décrire le document que tu veux ? "
            "(objectif, contexte, secteur, public cible…) "
            "Plus tu es précis, meilleur sera le résultat !"
        )

    if len(text) < 12:
        return (
            "Ta demande est un peu courte. Peux-tu me donner plus de détails ? "
            "(secteur, public cible, points essentiels à couvrir)"
        )

    # Question courte sans contexte
    if text.endswith("?") and len(text.split()) < 8:
        return (
            "Je vois que tu poses une question. Pour générer un document de qualité, "
            "décris-moi plutôt le livrable attendu : sujet, objectif, destinataires, "
            "points clés."
        )

    return None


def _build_suggestions(doc_type: str) -> list[Suggestion]:
    """Construit les suggestions post-génération (côté Python, pas LLM)."""
    return [
        Suggestion(id="1", label="Si tu veux je peux te : modifier une section"),
        Suggestion(id="2", label="Si tu veux je peux te : ajouter plus de détails"),
        Suggestion(id="3", label="Si tu veux je peux te : adapter le ton ou le style"),
    ]


async def _is_follow_up(ctx: AgentContext, llm: LLMProvider) -> bool:
    """Classifie si le message est un suivi d'un document existant ou une nouvelle demande.

    Logique en deux temps :
    1. Vérification rapide sans LLM : si pas d'historique assistant → NEW (gratuit).
       Si signal de format explicite (en pdf, mets en forme) → NEW (gratuit).
    2. Classification LLM légère (temperature=0, max_tokens=5) pour tous les autres cas.
       Coût : ~50 tokens, ~0.3s. Appelée uniquement si l'historique contient une réponse.

    Fallback sécurisé : si le LLM échoue → FOLLOWUP (mieux que régénérer un document
    entier par erreur).
    """
    # Pas d'historique assistant → forcément une nouvelle demande, pas d'appel LLM
    has_previous_response = any(h.get("role") == "assistant" for h in ctx.history)
    if not has_previous_response:
        return False

    # Signaux de format/export explicites → NEW sans appel LLM
    lower = ctx.message.lower()
    if any(signal in lower for signal in DOCUMENT_FORMAT_SIGNALS):
        return False

    # Classification LLM pour tous les cas ambigus
    doc_type = (
        ctx.goal_context.get("document_type")
        or detect_doc_type(ctx.message)
        or "document"
    )

    messages = [
        {
            "role": "system",
            "content": (
                "Tu classifies des messages utilisateur. "
                "Réponds UNIQUEMENT par le mot FOLLOWUP ou le mot NEW.\n\n"
                "FOLLOWUP : l'utilisateur demande une modification, amélioration, "
                "correction, reformulation ou pose une question sur un document "
                "qu'il a déjà reçu — même s'il utilise des verbes comme "
                "« refais », « génère une version », « améliore », « raccourcis », "
                "« adapte », « traduis », « reformule ».\n"
                "NEW : l'utilisateur demande un document entièrement nouveau, "
                "sans lien avec ce qui a déjà été généré."
            ),
        },
        {
            "role": "user",
            "content": (
                f"L'utilisateur vient de recevoir un {doc_type}.\n"
                f"Son nouveau message : \"{ctx.message}\""
            ),
        },
    ]

    try:
        raw = await llm.complete(messages, temperature=0.0, max_tokens=5)
        return "FOLLOWUP" in raw.strip().upper()
    except Exception:
        logger.warning(
            "[DocumentAgent] _is_follow_up: LLM call failed — defaulting to FOLLOWUP"
        )
        return True  # Sécurisé : conversation plutôt que régénération complète


_FOLLOWUP_SYSTEM = (
    "Tu es l'assistant document de Malayka. L'utilisateur a déjà reçu un document "
    "et te demande une modification ou a une question de suivi.\n\n"
    "Réponds de manière conversationnelle en français. "
    "Si l'utilisateur demande des modifications, explique ce que tu peux faire "
    "et demande les précisions nécessaires.\n"
    "Ne génère PAS un nouveau document complet. Réponds simplement à sa demande."
)


class DocumentAgent(SpecializedAgent):
    """Agent spécialisé pour la génération de documents.

    En mode process() (non-streaming) :
    - Nouvelle demande → génère le document, met dans deliverables
    - Follow-up → répond en conversationnel, PAS de deliverables

    En mode streaming (via ExecutionEngine), la génération section-par-section
    est orchestrée par l'engine, pas par cet agent.
    """

    AGENT_ID = "document"
    SYSTEM_PROMPT = ""  # Défini dynamiquement dans process()

    async def process(self, ctx: AgentContext) -> AgentResponse:
        # Follow-up : répondre en conversationnel, pas de nouveau document
        if await _is_follow_up(ctx, self.llm):
            return await self._process_followup(ctx)

        # Pré-vérification : le message est-il assez précis ?
        clarification = _needs_clarification(ctx.message)
        if clarification:
            return AgentResponse(
                explanation=clarification,
                clarifications=[clarification],
                agent_id=self.AGENT_ID,
            )

        # Déterminer le type de document
        doc_type = ctx.goal_context.get("document_type") or detect_doc_type(ctx.message)
        config = DELIVERABLE_CONFIGS.get(doc_type or "", DEFAULT_CONFIG)

        # Construire le prompt système enrichi
        self.SYSTEM_PROMPT = config.system_prompt

        # Construire les messages — profil + contexte + message user
        messages = self._build_messages(ctx)

        # Appel LLM unique — retourne du Markdown pur
        raw = await self.llm.complete(messages, temperature=0.35)
        content = raw.strip()

        return AgentResponse(
            explanation=f"Voici ton {config.description} ! N'hésite pas à me demander des modifications.",
            deliverables=[content],
            suggestions=_build_suggestions(config.doc_type),
            agent_id=self.AGENT_ID,
        )

    async def _process_followup(self, ctx: AgentContext) -> AgentResponse:
        """Traite un message de suivi sans regénérer de document."""
        self.SYSTEM_PROMPT = _FOLLOWUP_SYSTEM
        messages = self._build_messages(ctx)
        raw = await self.llm.complete(messages)
        return AgentResponse(
            explanation=raw.strip(),
            agent_id=self.AGENT_ID,
        )
