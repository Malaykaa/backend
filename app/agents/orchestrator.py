"""Orchestrateur — route le message vers le bon agent et retourne une AgentResponse.

Deux modes d'exécution :
- route()        : synchrone, retourne une AgentResponse (existant, inchangé)
- stream_route() : async iterator, yield des ProgressEvent (nouveau, pour SSE)

Utilise le Triage unifié (remplace IntentClassifier + Planner) :
- 1 appel LLM au lieu de 2 sur un nouveau thread
- ~1-2s gagnées par message
"""

import logging
from typing import AsyncIterator

from app.agents.base import AgentContext, AgentResponse, SpecializedAgent
from app.agents.career_agent import CareerAgent
from app.agents.deliverable_configs import detect_doc_type
from app.agents.document_agent import DocumentAgent, DOCUMENT_FORMAT_SIGNALS
from app.agents.events import EventType, ProgressEvent
from app.agents.exam_agent import ExamAgent
from app.agents.execution_engine import ExecutionEngine
from app.agents.freelance_agent import FreelanceAgent
from app.agents.funding_agent import FundingAgent
from app.agents.scholarship_agent import ScholarshipAgent
from app.agents.search_trigger import build_search_query, should_search
from app.agents.study_grant_agent import StudyGrantAgent
from app.agents.tender_agent import TenderAgent
from app.agents.triage import PlanDecision, Triage, TriageResult
from app.llm.base import LLMProvider
from app.services.perplexity_search_service import get_perplexity_search_service

logger = logging.getLogger(__name__)


class _GenericAgent(SpecializedAgent):
    """Agent générique — fallback quand aucun agent spécialisé n'est enregistré.

    Accepte un agent_id dynamique (contrairement aux agents spécialisés
    qui ont un AGENT_ID fixe en attribut de classe).
    """

    SYSTEM_PROMPT = """\
## Cas 1 — Accueil (salutation ou besoin non précisé)
Accueille l'utilisateur avec son prénom s'il est connu (champ first_name du profil).
Présente les domaines où Malayka peut l'accompagner :
- Examens & concours
- Bourses d'études & stages
- Financement & subventions de projet
- Appels d'offres & marchés publics
- Études à l'étranger
- Carrière, emploi & reconversion
- Missions freelance
- Documents professionnels (CV, lettres, business plans)
Pose une question ouverte pour identifier son objectif prioritaire.

## Cas 2 — Besoin légitime hors spécialités actuelles
L'utilisateur exprime un besoin réel (développement personnel, finances personnelles, \
bien-être, langues, sport, parentalité, relations…) qui ne correspond à aucun des domaines \
couverts par Malayka aujourd'hui.

Procède en 3 temps :
1. **Reconnaître** : valide le besoin sans le minimiser. C'est un objectif réel et important.
2. **Être honnête** : explique en une phrase que ce domaine n'est pas encore dans les \
spécialités de Malayka — sans t'excuser excessivement.
3. **Proposer un pont** : cherche dans les domaines de Malayka ce qui touche de près \
l'intention de l'utilisateur et propose-le concrètement. \
Exemples de raisonnement (adapte à chaque situation) :
   - Finances personnelles → si lié à un projet ou une activité : Financement / Freelance
   - Développement personnel → si lié à la carrière ou un concours : Carrière / Exam
   - Communication, prise de parole → si lié à un entretien ou projet pro : Carrière / Document
   - Langues → si pour accéder à une bourse ou étudier à l'étranger : Bourses / Études
   - Bien-être, stress → si lié à une préparation d'examen ou pression pro : Exam / Carrière
   - Entrepreneuriat social → Financement / Freelance / Appels à projet
   Si aucun pont pertinent n'existe, sois honnête : dis-le clairement et oriente vers \
   une ressource externe adaptée si tu en connais une fiable.
4. Termine par une question ouverte pour savoir si ce pont correspond à son besoin.

## Cas 3 — Hors-scope sans angle de rapprochement
Si la demande est totalement sans rapport (cuisine, sport de loisir, météo, divertissement…) \
et qu'aucun pont vers Malayka n'est pertinent : redirige brièvement et propose de revenir \
sur un objectif professionnel ou académique.

## Règle transversale
Ne donne JAMAIS une réponse complète sur un domaine hors spécialités — même si tu pourrais. \
Ton rôle est d'orienter, pas de te substituer à un expert d'un autre domaine."""

    def __init__(self, agent_id: str, llm: LLMProvider) -> None:
        super().__init__(llm)
        self.AGENT_ID = agent_id


# Registry des agents spécialisés
_AGENT_REGISTRY: dict[str, type | None] = {
    "exam": ExamAgent,
    "scholarship": ScholarshipAgent,
    "funding": FundingAgent,
    "freelance": FreelanceAgent,
    "tender": TenderAgent,
    "study_grant": StudyGrantAgent,
    "career": CareerAgent,
    "document": DocumentAgent,
    "free": None,
}


class Orchestrator:
    """Point d'entrée unique : triage → route vers agent → AgentResponse.

    Deux modes :
    - route()        : retourne directement une AgentResponse (mode classique)
    - stream_route() : yield des ProgressEvent pour SSE (mode streaming)
    """

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm
        self.triage = Triage(llm)
        self.engine = ExecutionEngine(llm, self._get_agent)

    async def _enrich_with_search(self, ctx: AgentContext) -> AgentContext:
        """Enrichit le contexte avec une recherche Perplexity si justifiée.

        Comportement défensif :
        - Ne lève JAMAIS d'exception (timeout, API error → log + return ctx intact).
        - Si search_results déjà présent dans goal_context → skip (idempotent).
        - Si le goal_type n'est pas éligible → skip immédiat (O(1)).
        """
        # Idempotence : ne pas re-chercher si déjà enrichi
        if ctx.goal_context.get("search_results"):
            return ctx

        trigger, model = should_search(ctx.message, ctx.goal_type)
        if not trigger:
            return ctx

        search_svc = get_perplexity_search_service()
        if not search_svc.available:
            return ctx

        query = build_search_query(ctx.message, ctx.goal_type, ctx.profile)
        try:
            result = await search_svc.search(query, model=model, goal_type=ctx.goal_type)
        except Exception as exc:
            # Filet de sécurité — ne doit jamais arriver (le service gère ses propres erreurs)
            logger.warning("[Orchestrator] search non-critique échouée: %s", exc)
            return ctx

        if result is None:
            return ctx

        enriched = {**ctx.goal_context, "search_results": result.to_goal_context()}
        return ctx.model_copy(update={"goal_context": enriched})

    async def route(self, ctx: AgentContext) -> AgentResponse:
        """Triage l'intention et délègue à l'agent approprié."""
        # Enrichissement search non-bloquant (timeout 8s max, fallback silencieux)
        ctx = await self._enrich_with_search(ctx)

        # Pré-check déterministe : demande explicite de document formaté
        # Bypass le triage LLM pour les cas évidents → économise tokens + fiable
        if self._is_document_request(ctx.message):
            agent = self._get_agent("document")
            return await agent.process(ctx)

        # Si le goal_type est déjà connu
        if ctx.goal_type:
            # Triage LLM pour détecter un changement de sujet (formel ou inféré)
            triage_result = await self.triage.analyze(ctx.message, ctx.history)

            # mode=generate a priorité absolue → DocumentAgent
            if triage_result.mode == "generate":
                agent = self._get_agent("document")
                ctx = self._enrich_ctx_for_generate(ctx, triage_result)
            elif triage_result.confidence >= 0.8 and triage_result.intent != ctx.goal_type:
                # L'utilisateur a clairement changé de sujet → rerouter
                agent = self._get_agent(triage_result.intent)
            else:
                # Même sujet ou pas assez confiant → continuer avec l'agent actuel
                agent = self._get_agent(ctx.goal_type)
            return await agent.process(ctx)

        # Triage unifié : intent + confidence + mode en 1 appel LLM
        result = await self.triage.analyze(ctx.message, ctx.history)

        # Seuil de confiance : rejeter UNIQUEMENT si c'est le tout premier message
        # et qu'il est incompréhensible. En conversation, ne jamais rejeter.
        has_conversation = any(
            h.get("role") == "assistant" for h in ctx.history
        )
        if not has_conversation and result.confidence < 0.6:
            return AgentResponse(
                explanation="Je ne suis pas sûr de bien comprendre votre demande.",
                clarifications=["Pouvez-vous préciser votre demande ?"],
                agent_id="clarification",
            )

        ctx = self._enrich_ctx_for_generate(ctx, result)
        agent = self._get_agent(result.intent)
        return await agent.process(ctx)

    async def stream_route(self, ctx: AgentContext) -> AsyncIterator[ProgressEvent]:
        """Version streaming de route() — yield des ProgressEvent pour SSE.

        Utilise le Triage unifié pour décider de l'intent ET du mode (direct/workflow),
        puis l'ExecutionEngine pour exécuter.
        """
        # Enrichissement search non-bloquant (timeout 8s max, fallback silencieux)
        ctx = await self._enrich_with_search(ctx)

        # Pré-check déterministe : demande explicite de document formaté
        if self._is_document_request(ctx.message):
            yield ProgressEvent(type=EventType.planning, content="")
            plan = PlanDecision(mode="direct", agent_type="document")
            async for event in self.engine.execute(plan, ctx):
                yield event
            return

        # Signal de début d'analyse — dots animés côté frontend, pas de texte
        yield ProgressEvent(type=EventType.planning, content="")

        # Si goal_type connu — triage LLM pour détecter changement de sujet
        if ctx.goal_type:
            triage_result = await self.triage.analyze(ctx.message, ctx.history)

            # mode=generate a priorité absolue → DocumentAgent
            if triage_result.mode == "generate":
                yield ProgressEvent(type=EventType.planning, content="")
                ctx = self._enrich_ctx_for_generate(ctx, triage_result)
                plan = triage_result.to_plan_decision()
                async for event in self.engine.execute(plan, ctx):
                    yield event
                return

            if triage_result.confidence >= 0.8 and triage_result.intent != ctx.goal_type:
                # Changement de sujet détecté → utiliser le nouveau plan
                plan = triage_result.to_plan_decision()
                async for event in self.engine.execute(plan, ctx):
                    yield event
                return

            # Même sujet ou pas assez confiant → direct avec l'agent actuel
            agent = self._get_agent(ctx.goal_type)
            result = await agent.process(ctx)
            yield ProgressEvent(
                type=EventType.done,
                agent_id=result.agent_id,
                agent_response=result,
            )
            return

        # Triage unifié : intent + confidence + mode en 1 appel
        triage_result = await self.triage.analyze(ctx.message, ctx.history)

        # Seuil de confiance : rejeter UNIQUEMENT si c'est le tout premier message
        # et qu'il est incompréhensible. En conversation, ne jamais rejeter.
        has_conversation = any(
            h.get("role") == "assistant" for h in ctx.history
        )
        if not has_conversation and triage_result.confidence < 0.6:
            yield ProgressEvent(
                type=EventType.done,
                agent_id="clarification",
                agent_response=AgentResponse(
                    explanation="Je ne suis pas sûr de bien comprendre votre demande.",
                    clarifications=["Pouvez-vous préciser votre demande ?"],
                    agent_id="clarification",
                ),
            )
            return

        # Construire le PlanDecision depuis le TriageResult
        plan = triage_result.to_plan_decision()
        ctx = self._enrich_ctx_for_generate(ctx, triage_result)

        if triage_result.mode == "generate":
            yield ProgressEvent(type=EventType.planning, content="")

        # Exécution via l'engine
        async for event in self.engine.execute(plan, ctx):
            yield event

    @staticmethod
    def _is_document_request(message: str) -> bool:
        """Détecte si le message demande explicitement un livrable/document formaté.

        Filet de sécurité déterministe avant le triage LLM :
        1. Signaux de format explicites ("fais un document", "en pdf"…)
        2. Verbe de création + type de document connu ("génère un cv", "rédige une lettre"…)
        """
        lower = message.lower()
        # 1. Signaux de format explicites
        if any(signal in lower for signal in DOCUMENT_FORMAT_SIGNALS):
            return True
        # 2. Verbe de création + type de document détecté
        creation_verbs = (
            "génère", "genere", "rédige", "redige", "crée", "cree",
            "fais-moi", "fais moi", "fais un", "fais une",
            "prépare", "prepare", "produis", "écris", "ecris",
        )
        if any(v in lower for v in creation_verbs) and detect_doc_type(lower):
            return True
        return False

    @staticmethod
    def _enrich_ctx_for_generate(ctx: AgentContext, triage_result: TriageResult) -> AgentContext:
        """Enrichit le contexte avec action_type quand mode=generate."""
        if triage_result.mode != "generate" or not triage_result.action_type:
            return ctx
        enriched_context = {**ctx.goal_context, "document_type": triage_result.action_type}
        return ctx.model_copy(update={"goal_context": enriched_context})

    def _get_agent(self, intent: str) -> SpecializedAgent:
        """Retourne l'agent spécialisé ou le GenericAgent en fallback."""
        registered_cls = _AGENT_REGISTRY.get(intent)
        if registered_cls is not None:
            return registered_cls(llm=self.llm)
        return _GenericAgent(agent_id=intent or "free", llm=self.llm)
