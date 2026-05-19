"""IntentExtractorService — extrait et stocke l'intention utilisateur par LLM.

Déclenchement automatique après EXTRACTION_THRESHOLD messages actifs dans
un thread lié à un objectif (goal). Re-extraction tous les RE_EXTRACTION_INTERVAL
messages supplémentaires pour affiner l'intention au fil de la conversation.

Résultat stocké dans user_intents et utilisé pour le matching d'offres.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.llm.base import LLMProvider
from app.models.chat import ChatThread
from app.models.user_intent import UserIntent
from app.repositories.chat_repo import ChatRepository
from app.repositories.intent_repo import IntentRepository

logger = logging.getLogger(__name__)

# ── Seuils ────────────────────────────────────────────────────────────────────
# Déclenche la première extraction après ce nombre de messages actifs (user + assistant)
# 8 messages = ~4 échanges de part et d'autre
EXTRACTION_THRESHOLD = 8

# Re-extrait tous les N messages supplémentaires après la première extraction
RE_EXTRACTION_INTERVAL = 6

# ── Types normalisés attendus du LLM ─────────────────────────────────────────
VALID_INTENT_TYPES = frozenset({
    "stage", "emploi", "bourse", "financement",
    "appel_offre", "formation", "reconversion",
    "entrepreneuriat", "partenariat", "autre",
})

# ── Prompt système ────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
Tu es un système d'analyse de conversation spécialisé dans l'extraction d'intention.
Ton unique rôle est d'analyser la conversation fournie et d'extraire ce que l'utilisateur veut réellement accomplir.

Réponds UNIQUEMENT avec un objet JSON valide. Aucun texte avant ou après le JSON.

Format attendu :
{
  "intent_summary": "Description claire, concise et précise de l'objectif réel de l'utilisateur (1-2 phrases maximum)",
  "intent_type": "l'un de : stage | emploi | bourse | financement | appel_offre | formation | reconversion | entrepreneuriat | partenariat | autre",
  "domain": "domaine ou secteur d'activité principal (ex: BTP, informatique, finance, santé, agriculture, éducation) ou null si non mentionné",
  "keywords": ["mot-clé 1", "mot-clé 2", "mot-clé 3"],
  "location": "pays, région ou ville souhaitée ou null si non mentionné",
  "level": "niveau académique ou professionnel (ex: licence, master, doctorat, PFE, junior, senior, débutant) ou null si non mentionné",
  "duration": "durée souhaitée (ex: 6 mois, 1 an, CDI, CDD, temps plein) ou null si non mentionné"
}

Règles strictes :
1. intent_summary : basé UNIQUEMENT sur les informations explicitement données dans la conversation
2. keywords : entre 3 et 8 mots-clés pertinents pour une recherche d'offres — privilégie les termes techniques et sectoriels
3. Mettre null pour tout champ dont l'information n'est pas présente dans la conversation
4. Ne JAMAIS inventer ou déduire des informations non dites par l'utilisateur
5. Si plusieurs intentions coexistent, retenir la principale et la plus récente
"""


class IntentExtractorService:
    """Extrait et persiste l'intention utilisateur d'un thread objectif."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._chat_repo = ChatRepository(db)
        self._intent_repo = IntentRepository(db)

    # ── API publique ──────────────────────────────────────────────────────────

    def should_extract(self, thread_id: uuid.UUID) -> bool:
        """Retourne True si le seuil d'extraction est atteint pour ce thread."""
        thread = self._chat_repo.get_by_id(thread_id)
        if not thread:
            return False

        # N'extraire que pour les threads liés à un objectif (goal)
        if not thread.goal_id:
            return False

        count = thread.message_count
        if count < EXTRACTION_THRESHOLD:
            return False

        existing = self._intent_repo.get_by_thread(thread_id)
        if existing is None:
            return True  # Première extraction

        # Re-extraction si assez de nouveaux messages depuis la dernière
        messages_since = count - existing.message_count_at_extraction
        return messages_since >= RE_EXTRACTION_INTERVAL

    async def maybe_extract(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        llm: LLMProvider,
    ) -> UserIntent | None:
        """Extrait et stocke l'intention si le seuil est atteint.

        Conçu pour un appel fire-and-forget depuis ChatService :
        toutes les exceptions sont capturées pour ne jamais bloquer le flux chat.
        Retourne None si seuil non atteint ou en cas d'erreur.
        """
        try:
            if not self.should_extract(thread_id):
                return None
            return await self._extract_and_store(thread_id, user_id, llm)
        except Exception:
            logger.warning(
                "Intent extraction failed silently for thread %s",
                thread_id,
                exc_info=True,
            )
            return None

    # ── Logique interne ───────────────────────────────────────────────────────

    async def _extract_and_store(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        llm: LLMProvider,
    ) -> UserIntent | None:
        """Charge la conversation, appelle le LLM, parse et persiste l'intention."""

        # Charger le thread avec son goal (joinedload pour éviter le lazy load)
        thread = self._db.execute(
            select(ChatThread)
            .options(joinedload(ChatThread.goal))
            .where(ChatThread.id == thread_id)
        ).unique().scalar_one_or_none()

        if not thread:
            return None

        # Récupérer tous les messages actifs en ordre chronologique
        messages = self._chat_repo.get_active_messages(thread_id)
        if not messages:
            return None

        # Construire le texte de conversation — filtrer les messages internes
        # (messages de contexte envoyés automatiquement, non visibles par l'utilisateur)
        conversation_lines: list[str] = []
        for m in messages:
            if m.payload and m.payload.get("is_internal"):
                continue
            role_label = "Utilisateur" if m.role.value == "user" else "Assistant"
            conversation_lines.append(f"{role_label} : {m.content}")

        # Minimum 4 lignes (2 user + 2 assistant) pour une extraction pertinente
        if len(conversation_lines) < 4:
            return None

        conversation_text = "\n".join(conversation_lines)

        # Ajouter le contexte du goal comme hint pour le LLM
        goal_hint = ""
        if thread.goal and thread.goal.context_data:
            preset_label = thread.goal.context_data.get("preset_label", "")
            if preset_label:
                goal_hint = f"Contexte de l'objectif déclaré : {preset_label}\n\n"

        user_message = (
            f"{goal_hint}"
            f"Conversation à analyser :\n\n{conversation_text}\n\n"
            "Extrais l'intention réelle de l'utilisateur en JSON."
        )

        # Appel LLM — température très basse pour une sortie déterministe
        raw_response = await llm.complete(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=400,
            temperature=0.1,
        )

        # Parser la réponse JSON (tolère les backticks Markdown et parasites)
        structured = _parse_json(raw_response)
        if not structured:
            logger.warning(
                "Could not parse intent JSON for thread %s. Raw: %.300s",
                thread_id,
                raw_response,
            )
            return None

        # Normaliser l'intent_type — fallback "autre" si valeur inconnue
        intent_type = structured.get("intent_type") or "autre"
        if intent_type not in VALID_INTENT_TYPES:
            intent_type = "autre"

        # Upsert en base
        intent = self._intent_repo.upsert(
            user_id=user_id,
            thread_id=thread_id,
            goal_id=thread.goal_id,
            intent_summary=structured.get("intent_summary") or "",
            intent_type=intent_type,
            domain=structured.get("domain"),
            keywords=structured.get("keywords") or [],
            location=structured.get("location"),
            level=structured.get("level"),
            duration=structured.get("duration"),
            raw_structured=structured,
            message_count=thread.message_count,
        )

        logger.info(
            "Intent extracted — thread=%s user=%s type=%s summary=%.120s (v%d)",
            thread_id,
            user_id,
            intent_type,
            intent.intent_summary,
            intent.version,
        )
        return intent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict | None:
    """Parse JSON depuis la réponse LLM.

    Tolère :
    - Blocs markdown ```json ... ```
    - Texte parasite avant/après le JSON
    - Espaces/retours à la ligne superflus
    """
    text = raw.strip()

    # Nettoyer les blocs ```json ... ``` ou ``` ... ```
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()

    # Extraire le premier objet JSON complet { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
