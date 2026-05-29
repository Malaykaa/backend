"""Moteur d'exécution — exécute un plan (direct, document, ou workflow multi-étapes).

Trois modes :
- direct   : appelle un agent, retourne la réponse → terminé.
- document : génère section par section avec progression (step_start/step_complete).
- workflow : itère sur les étapes, appelle un agent par étape,
             passe les résultats précédents en contexte.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Callable

from app.agents.base import AgentBase, AgentContext, AgentResponse, Suggestion
from app.agents.guardrails import GUARDRAILS_SHORT
from app.agents.deliverable_configs import (
    DELIVERABLE_CONFIGS,
    DEFAULT_CONFIG,
    DeliverableConfig,
    detect_doc_type,
)
from app.agents.events import EventType, ProgressEvent
from app.agents.triage import PlanDecision
from app.agents.types import AgentMode, GoalType
from app.llm.base import LLMProvider
from app.llm.continuation import complete_with_continuation

logger = logging.getLogger(__name__)

# Labels lisibles pour le frontend
_AGENT_LABELS: dict[str, str] = {
    "exam": "Préparation examen",
    "scholarship": "Recherche de bourses",
    "funding": "Recherche de financement",
    "tender": "Appels d'offres",
    "study_grant": "Bourse d'étude",
    "career": "Carrière & emploi",
    "document": "Génération de document",
    "free": "Assistant général",
}


def _format_profile(profile: dict) -> str:
    """Formate le profil utilisateur en texte lisible."""
    if not profile:
        return ""
    lines = []
    for key, val in profile.items():
        if val:
            lines.append(f"{key}: {val}")
    return ", ".join(lines)


def _build_conversation_context(history: list[dict]) -> str:
    """Construit un résumé textuel de l'historique de conversation.

    Inclut le résumé compressé (system) et les échanges user/assistant récents.
    Le champ `content` en DB est toujours la courte explication (pas le document
    complet), donc pas de risque de token bloat en incluant tous les messages.
    """
    if not history:
        return ""
    parts = []
    for msg in history:
        role = msg.get("role", "")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            parts.append(f"[Résumé de la conversation]\n{content[:800]}")
        elif role == "user":
            parts.append(f"Utilisateur : {content[:500]}")
        elif role in ("assistant", "bot"):
            parts.append(f"Assistant : {content[:500]}")
    return "\n".join(parts)[:4000]


def _build_section_prompt(
    instruction: str,
    user_message: str,
    profile_summary: str,
    objective_context: str | None,
    key_facts_summary: str,
    conversation_context: str = "",
) -> str:
    """Construit le prompt utilisateur pour une section de document."""
    parts = [f"Demande de l'utilisateur : {user_message}"]

    if profile_summary:
        parts.append(f"Profil : {profile_summary}")

    if objective_context:
        parts.append(f"Contexte : {objective_context}")

    if conversation_context:
        parts.append(
            f"Contexte de la conversation (informations fournies par l'utilisateur) :\n{conversation_context}"
        )

    if key_facts_summary:
        parts.append(
            f"Décisions déjà établies dans ce document :\n{key_facts_summary}"
        )

    parts.append(f"INSTRUCTION POUR CETTE SECTION :\n{instruction}")

    return "\n\n".join(parts)


async def _extract_key_facts(
    section_label: str,
    section_content: str,
    llm: LLMProvider,
) -> str:
    """Extrait les faits clés d'une section générée (chiffres, noms, hypothèses).

    Appel LLM léger (temperature=0, max_tokens=150). Utilisé pour alimenter le
    contexte des sections suivantes sans envoyer le texte brut complet.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "Tu extrais les faits factuels importants d'un texte. "
                "Réponds UNIQUEMENT avec 3 à 5 bullet points commençant par •. "
                "Inclus uniquement : chiffres précis, noms propres, hypothèses centrales, dates clés. "
                "Maximum 120 mots. Aucun texte avant ou après les bullet points."
            ),
        },
        {
            "role": "user",
            "content": f"Section : {section_label}\n\n{section_content[:2000]}",
        },
    ]
    try:
        raw = await llm.complete(messages, temperature=0.0, max_tokens=150)
        return raw.strip()
    except Exception:
        return ""


async def _check_coherence(
    accumulated_facts: str,
    llm: LLMProvider,
) -> str | None:
    """Vérifie la cohérence inter-sections sur la base des faits accumulés.

    Retourne None si cohérent, ou un bloc markdown ## ⚠️ Points à vérifier.
    Appel LLM unique (temperature=0.1, max_tokens=600).
    """
    messages = [
        {
            "role": "system",
            "content": (
                "Tu vérifies la cohérence des faits extraits de différentes sections d'un document. "
                "Recherche : chiffres contradictoires, dates incompatibles, hypothèses conflictuelles.\n\n"
                "Si tout est cohérent → réponds UNIQUEMENT : COHÉRENT\n"
                "Sinon → liste les problèmes UNIQUEMENT sous ce format :\n"
                "[Section X ↔ Section Y] : description courte de l'incohérence\n\n"
                "Maximum 5 incohérences. Pas de texte explicatif, uniquement les items listés ou COHÉRENT."
            ),
        },
        {
            "role": "user",
            "content": f"Faits extraits par section :\n\n{accumulated_facts}",
        },
    ]
    try:
        raw = await llm.complete(messages, temperature=0.1, max_tokens=600)
        result = raw.strip()
        if "COHÉRENT" in result.upper():
            return None
        return (
            "## ⚠️ Points à vérifier\n\n"
            "*Ces éléments méritent une relecture — des incohérences potentielles ont été détectées :*\n\n"
            + result
        )
    except Exception:
        return None


class ExecutionEngine:
    """Exécute un PlanDecision et yield des ProgressEvents."""

    def __init__(
        self,
        llm: LLMProvider,
        get_agent: Callable[[str], AgentBase],
    ) -> None:
        self.llm = llm
        self._get_agent = get_agent

    async def execute(
        self,
        plan: PlanDecision,
        ctx: AgentContext,
    ) -> AsyncIterator[ProgressEvent]:
        """Point d'entrée : exécute le plan et yield la progression."""
        # Mode document : section-par-section avec progression
        # Sauf si c'est un follow-up (modification, question de suivi)
        if plan.mode == AgentMode.DIRECT and plan.agent_type == GoalType.DOCUMENT:
            from app.agents.document_agent import _is_follow_up

            if await _is_follow_up(ctx, self.llm):
                # Follow-up → répondre en conversationnel (pas de re-génération)
                async for event in self._execute_direct(plan, ctx):
                    yield event
            else:
                async for event in self._execute_document(ctx):
                    yield event
            return

        # Mode workflow : multi-agents séquentiels
        if plan.mode == AgentMode.WORKFLOW and plan.steps:
            async for event in self._execute_workflow(plan, ctx):
                yield event
            return

        # Mode direct : un seul agent
        async for event in self._execute_direct(plan, ctx):
            yield event

    async def _execute_direct(
        self,
        plan: PlanDecision,
        ctx: AgentContext,
    ) -> AsyncIterator[ProgressEvent]:
        """Mode direct : un seul agent, comme avant."""
        agent_type = plan.agent_type or GoalType.FREE
        agent = self._get_agent(agent_type)
        result = await agent.process(ctx)

        yield ProgressEvent(
            type=EventType.done,
            agent_id=result.agent_id,
            agent_response=result,
        )

    async def _execute_document(
        self,
        ctx: AgentContext,
    ) -> AsyncIterator[ProgressEvent]:
        """Mode document : génère section par section avec progression.

        - Détecte le type de document
        - Pré-vérifie si le message est assez précis
        - Boucle sur les sections en émettant step_start/step_complete
        - Passe le contenu des sections précédentes au LLM pour la cohérence
        - Retourne du Markdown pur (pas de JSON)
        """
        from app.agents.document_agent import _needs_clarification

        # Pré-vérification
        clarification = _needs_clarification(ctx.message)
        if clarification:
            yield ProgressEvent(
                type=EventType.done,
                agent_id="document",
                agent_response=AgentResponse(
                    explanation=clarification,
                    clarifications=[clarification],
                    agent_id="document",
                ),
            )
            return

        # Résoudre le type de document et sa config
        doc_type = ctx.goal_context.get("document_type") or detect_doc_type(ctx.message)
        config = DELIVERABLE_CONFIGS.get(doc_type or "")

        if config is None:
            # Type non reconnu → Claude planifie les sections dynamiquement
            config, sections = await self._plan_sections_dynamically(ctx, doc_type)
        else:
            sections = config.sections

        total = len(sections)

        # Annoncer la planification
        yield ProgressEvent(
            type=EventType.planning,
            content=f"Génération de ton {config.description} en {total} sections…",
            total_steps=total,
        )

        profile_summary = _format_profile(ctx.profile)
        objective_context = ctx.goal_context.get("objective_context")
        # Contexte de la conversation : tout ce que l'utilisateur a partagé avant
        # cette demande de génération (réponses au questionnaire d'accueil, détails
        # du projet, etc.) — injecté dans chaque section pour personnaliser le document.
        conversation_context = _build_conversation_context(ctx.history)
        generated_sections: list[str] = []
        # (section_label, key_facts_text) — alimenté après chaque section générée
        accumulated_key_facts: list[tuple[str, str]] = []

        for i, section in enumerate(sections):
            # Notifier le début de la section
            yield ProgressEvent(
                type=EventType.step_start,
                content=section.label,
                agent_id="document",
                step_index=i,
                total_steps=total,
            )

            # Résumé structuré des décisions établies dans les sections précédentes
            # (remplace l'envoi du texte brut — D1)
            key_facts_summary = "\n\n".join(
                f"[{label}]\n{facts}"
                for label, facts in accumulated_key_facts
                if facts
            )
            user_prompt = _build_section_prompt(
                instruction=section.instruction,
                user_message=ctx.message,
                profile_summary=profile_summary,
                objective_context=objective_context,
                key_facts_summary=key_facts_summary,
                conversation_context=conversation_context,
            )

            messages = [
                {"role": "system", "content": config.system_prompt},
                {"role": "system", "content": GUARDRAILS_SHORT},
            ]
            if profile_summary:
                messages.append({
                    "role": "system",
                    "content": f"Profil de l'utilisateur : {profile_summary}",
                })
            messages.append({"role": "user", "content": user_prompt})

            try:
                # complete_with_continuation relance automatiquement si la section
                # est tronquée (max 3 relances avec "Continue où tu t'es arrêté.")
                section_content = await complete_with_continuation(
                    self.llm,
                    messages,
                    temperature=0.35,
                    max_tokens=section.max_tokens,
                )
                section_content = section_content.strip()
                generated_sections.append(section_content)
                # Extraire les faits clés pour alimenter les sections suivantes (D1)
                facts = await _extract_key_facts(section.label, section_content, self.llm)
                accumulated_key_facts.append((section.label, facts))
            except Exception as exc:
                logger.error(
                    "Document section '%s' failed: %s", section.id, exc
                )
                section_content = f"## {section.label}\n\n*[Cette section n'a pas pu être générée.]*"
                generated_sections.append(section_content)
                accumulated_key_facts.append((section.label, ""))

            # Notifier la fin de la section
            yield ProgressEvent(
                type=EventType.step_complete,
                content=section.label,
                agent_id="document",
                step_index=i,
                total_steps=total,
            )

        # Passe de cohérence finale (D2) — uniquement si ≥ 3 sections générées
        coherence_note: str | None = None
        if len(sections) >= 3:
            facts_text = "\n\n".join(
                f"[{label}]\n{facts}"
                for label, facts in accumulated_key_facts
                if facts
            )
            if facts_text:
                coherence_note = await _check_coherence(facts_text, self.llm)
                if coherence_note:
                    logger.info("[ExecutionEngine] Incohérences détectées — ajout bloc ⚠️")

        # Assembler le document final
        full_content = "\n\n---\n\n".join(generated_sections)
        if coherence_note:
            full_content += f"\n\n---\n\n{coherence_note}"

        response = AgentResponse(
            explanation=(
                f"Voici ton {config.description} ! "
                "N'hésite pas à me demander des modifications."
            ),
            deliverables=[full_content],
            suggestions=[
                Suggestion(id="1", label=f"Modifier une section de ce {config.description}"),
                Suggestion(id="2", label="Ajouter plus de détails sur un point précis"),
                Suggestion(id="3", label="Adapter le ton ou le style du document"),
            ],
            agent_id="document",
        )

        yield ProgressEvent(
            type=EventType.done,
            agent_id="document",
            agent_response=response,
        )

    async def _plan_sections_dynamically(
        self,
        ctx: AgentContext,
        doc_type: str | None,
    ) -> tuple[DeliverableConfig, tuple[DeliverableSection, ...]]:
        """Demande à Claude de planifier les sections d'un document non reconnu.

        Retourne un DeliverableConfig ad-hoc avec des sections personnalisées.
        Fallback sur DEFAULT_CONFIG si le LLM échoue ou retourne des sections invalides.
        """
        import json as _json

        conversation_ctx = _build_conversation_context(ctx.history)
        context_block = (
            f"\nContexte de la conversation :\n{conversation_ctx}\n"
            if conversation_ctx else ""
        )

        prompt = (
            f"L'utilisateur demande : {ctx.message}\n{context_block}\n"
            "Planifie les sections idéales pour ce document.\n"
            "Retourne UNIQUEMENT un JSON valide avec ce format :\n"
            '{"description": "nom court du document", "sections": ['
            '{"label": "Titre de section", "instruction": "Ce que cette section doit contenir (2-3 phrases précises)", "max_tokens": 900}'
            "]}\n\n"
            "Règles :\n"
            "- Entre 4 et 8 sections maximum\n"
            "- Sections ordonnées logiquement (intro → développement → conclusion)\n"
            "- max_tokens entre 600 et 1200 selon la densité attendue\n"
            "- Instructions précises et actionnables pour chaque section\n"
            "- Réponds UNIQUEMENT en JSON, aucun texte autour"
        )

        try:
            raw = await self.llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Tu es un expert en documentation professionnelle. "
                            "Tu planifies la structure de documents de manière optimale. "
                            "Tu réponds UNIQUEMENT en JSON valide, jamais en texte libre."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1200,
            )

            # Nettoyer les éventuels blocs markdown
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[:-1])

            data = _json.loads(cleaned.strip())
            description = data.get("description", doc_type or "document")
            raw_sections = data.get("sections", [])

            if not raw_sections or not isinstance(raw_sections, list):
                raise ValueError("sections vides ou invalides")

            sections: list[DeliverableSection] = []
            for i, s in enumerate(raw_sections[:8]):
                label = str(s.get("label", f"Section {i+1}")).strip()
                instruction = str(s.get("instruction", f"Rédige la section {label}.")).strip()
                max_tok = int(s.get("max_tokens", 900))
                max_tok = max(400, min(max_tok, 1400))
                sections.append(DeliverableSection(
                    id=f"dynamic_{i}",
                    label=label,
                    instruction=instruction,
                    max_tokens=max_tok,
                ))

            if not sections:
                raise ValueError("aucune section valide parsée")

            dynamic_config = DeliverableConfig(
                doc_type=doc_type or "document_general",
                description=description,
                system_prompt=(
                    "Tu es un rédacteur professionnel expert. "
                    "Tu produis des documents clairs, structurés et adaptés à leur contexte.\n\n"
                    "RÈGLES DE SORTIE :\n"
                    "• Réponds en markdown structuré avec titres (##, ###) et listes à puces.\n"
                    "• Langue : français.\n"
                    "• Sois précis, concret et actionnable. Évite le remplissage.\n"
                    "• Utilise les informations du profil utilisateur pour personnaliser."
                ),
                sections=tuple(sections),
            )
            logger.info(
                "[ExecutionEngine] sections dynamiques planifiées pour '%s': %d sections",
                doc_type, len(sections),
            )
            return dynamic_config, tuple(sections)

        except Exception as exc:
            logger.warning(
                "[ExecutionEngine] planification dynamique échouée pour '%s': %s — fallback DEFAULT_CONFIG",
                doc_type, exc,
            )
            return DEFAULT_CONFIG, DEFAULT_CONFIG.sections

    async def _execute_workflow(
        self,
        plan: PlanDecision,
        ctx: AgentContext,
    ) -> AsyncIterator[ProgressEvent]:
        """Mode workflow : exécute les étapes séquentiellement."""
        total = len(plan.steps)

        yield ProgressEvent(
            type=EventType.planning,
            content=f"J'ai identifié {total} étapes pour répondre à ta demande.",
            total_steps=total,
        )

        previous_results: list[AgentResponse] = []

        for i, step in enumerate(plan.steps):
            label = _AGENT_LABELS.get(step.agent_type, step.agent_type)

            # Notifier le début de l'étape
            yield ProgressEvent(
                type=EventType.step_start,
                content=f"{label} : {step.task}",
                agent_id=step.agent_type,
                step_index=i,
                total_steps=total,
            )

            # Construire le contexte enrichi pour cette étape
            step_ctx = self._build_step_context(ctx, step.task, previous_results)

            # Exécuter l'agent (avec fallback si erreur)
            agent = self._get_agent(step.agent_type)

            try:
                result = await agent.process(step_ctx)
            except Exception as exc:
                logger.error("Workflow step %d (%s) failed: %s", i, step.agent_type, exc)
                result = AgentResponse(
                    explanation="Cette étape n'a pas pu être complétée (erreur temporaire).",
                    agent_id=step.agent_type,
                )
                yield ProgressEvent(
                    type=EventType.step_complete,
                    content=f"[Erreur] {label} — passage à l'étape suivante",
                    agent_id=step.agent_type,
                    step_index=i,
                    total_steps=total,
                )
                previous_results.append(result)
                continue

            previous_results.append(result)

            # Notifier la fin de l'étape
            yield ProgressEvent(
                type=EventType.step_complete,
                content=result.explanation[:200],
                agent_id=result.agent_id,
                step_index=i,
                total_steps=total,
            )

        # Synthétiser tous les résultats en une réponse finale
        final = await self._synthesize(previous_results, ctx)

        yield ProgressEvent(
            type=EventType.done,
            agent_id="workflow",
            agent_response=final,
        )

    def _build_step_context(
        self,
        original_ctx: AgentContext,
        task: str,
        previous_results: list[AgentResponse],
    ) -> AgentContext:
        """Enrichit le contexte avec les résultats des étapes précédentes."""
        # Construire un résumé des étapes déjà faites
        history = list(original_ctx.history)

        if previous_results:
            summary_parts = []
            for r in previous_results:
                summary_parts.append(f"[{r.agent_id}] {r.explanation[:300]}")
            summary = "\n".join(summary_parts)
            history.append({
                "role": "system",
                "content": f"Résultats des étapes précédentes :\n{summary}",
            })

        return AgentContext(
            user_id=original_ctx.user_id,
            message=task,
            history=history,
            profile=original_ctx.profile,
            goal_type=None,  # Laisser l'agent traiter selon sa spécialité
            goal_context=original_ctx.goal_context,
        )

    async def _synthesize(
        self,
        results: list[AgentResponse],
        ctx: AgentContext,
    ) -> AgentResponse:
        """Synthétise les résultats de toutes les étapes en une AgentResponse unifiée."""
        # Combiner les explanations
        explanations = []
        all_steps = []
        all_suggestions = []
        all_sources = []
        all_deliverables = []

        for r in results:
            explanations.append(r.explanation)
            all_steps.extend(r.steps)
            all_suggestions.extend(r.suggestions)
            all_sources.extend(r.sources)
            all_deliverables.extend(r.deliverables)

        # Renuméroter les étapes
        for i, step in enumerate(all_steps):
            step.order = i + 1

        # Générer une synthèse via LLM si on a plusieurs résultats
        if len(results) > 1:
            combined_text = "\n\n".join(explanations)
            synthesis = await self._llm_synthesize(combined_text, ctx.message)
        else:
            synthesis = explanations[0] if explanations else ""

        return AgentResponse(
            explanation=synthesis,
            steps=all_steps,
            suggestions=all_suggestions,
            sources=all_sources,
            deliverables=all_deliverables,
            agent_id="workflow",
        )

    async def _llm_synthesize(self, combined_text: str, user_message: str) -> str:
        """Résume les résultats de plusieurs agents en une réponse cohérente."""
        messages = [
            {
                "role": "system",
                "content": (
                    "Tu es l'assistant Malayka. Plusieurs experts ont travaillé "
                    "sur la demande de l'utilisateur. Synthétise leurs résultats "
                    "en une réponse claire et cohérente en français. "
                    "Sois concis mais complet."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Demande originale : {user_message}\n\n"
                    f"Résultats des experts :\n{combined_text}\n\n"
                    "Synthétise en une réponse unifiée."
                ),
            },
        ]
        return await complete_with_continuation(self.llm, messages)
