"""IntentExtractorService — extrait et stocke l'intention utilisateur par LLM.

Logique d'extraction progressive en 3 niveaux :

  Niveau 1 — Précoce (≥ 2 messages) :
    Si l'utilisateur exprime une intention explicite dès les premiers messages
    ("je cherche", "je veux", "mon objectif est…"), l'extraction est déclenchée
    immédiatement sans attendre d'accumuler des échanges.

  Niveau 2 — Standard (≥ 4 messages) :
    Après 2 échanges complets, l'extraction se déclenche systématiquement même
    sans signal explicite — suffisamment de contexte pour une extraction fiable.

  Niveau 3 — Évolutif (re-extraction tous les 4 messages) :
    L'intention est raffinée régulièrement au fil de la conversation pour
    intégrer les précisions apportées par l'utilisateur.

Résultat stocké dans user_intents et utilisé pour le matching d'offres.
"""

from __future__ import annotations

import json
import logging
import re
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

# Niveau 1 : extraction précoce si signal explicite détecté
EARLY_EXTRACTION_THRESHOLD = 2    # min messages requis (1 user + 1 assistant)

# Niveau 2 : extraction systématique après ce nombre de messages
STANDARD_EXTRACTION_THRESHOLD = 4 # ~2 échanges complets

# Niveau 3 : re-extraction régulière après la première extraction
RE_EXTRACTION_INTERVAL = 4

# ── Détection de signal d'intention explicite ─────────────────────────────────
# Regex compilé une seule fois. Détecte les déclarations directes d'objectif
# dans les premiers messages utilisateur, sans appel LLM.
_INTENT_SIGNAL_RE = re.compile(
    r"""
    \b(?:
        # Français — déclarations directes
        je\s+cherche[sz]?         |
        je\s+recherche[sz]?       |
        je\s+veux                 |
        je\s+souhaite[sz]?        |
        je\s+voudrais             |
        j['']aimerais             |
        j['']ai\s+besoin          |
        j['']espère               |
        mon\s+objectif            |
        mon\s+but\s+est           |
        je\s+suis\s+\w+\s+la\s+recherche |
        postuler\s+(?:à|pour|sur) |
        je\s+vise                 |
        trouver\s+un[e]?\s+\w+    |
        décrocher\s+un[e]?        |
        obtenir\s+un[e]?          |
        m['']inscrire             |
        me\s+reconvertir          |
        candidater\s+(?:à|pour)   |
        préparer?\s+(?:le|un|mon|la|une) |
        # English — explicit declarations
        i['']m\s+looking\s+for    |
        i\s+want\s+to             |
        i\s+need\s+(?:a|to|an)    |
        i\s+would\s+like          |
        i['']d\s+like\s+to        |
        i['']m\s+seeking          |
        my\s+goal\s+is            |
        i\s+hope\s+to             |
        apply\s+for               |
        looking\s+for\s+a
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

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
        """Retourne True si les conditions d'extraction sont réunies.

        Applique la logique progressive en 3 niveaux :
          - Niveau 1 : ≥ 2 messages + signal explicite détecté → extraction précoce
          - Niveau 2 : ≥ 4 messages → extraction systématique
          - Niveau 3 : re-extraction tous les RE_EXTRACTION_INTERVAL messages
        """
        thread = self._chat_repo.get_by_id(thread_id)
        if not thread or not thread.goal_id:
            return False

        count = thread.message_count

        existing = self._intent_repo.get_by_thread(thread_id)
        if existing is not None:
            # Re-extraction : suffisamment de nouveaux messages depuis la dernière ?
            return (count - existing.message_count_at_extraction) >= RE_EXTRACTION_INTERVAL

        # Première extraction — logique progressive
        if count < EARLY_EXTRACTION_THRESHOLD:
            return False  # Trop tôt dans tous les cas

        if count < STANDARD_EXTRACTION_THRESHOLD:
            # Niveau 1 : extraire seulement si signal explicite présent
            return self._has_early_intent_signal(thread_id)

        # Niveau 2 : suffisamment d'échanges → extraction systématique
        return True

    def _has_early_intent_signal(self, thread_id: uuid.UUID) -> bool:
        """Détecte si les premiers messages utilisateur contiennent une
        déclaration d'intention explicite (sans appel LLM).

        Examine les 3 premiers messages visibles de l'utilisateur.
        Les messages internes (is_internal) sont ignorés car ils sont
        générés automatiquement et ne reflètent pas la voix de l'utilisateur.
        """
        messages = self._chat_repo.get_active_messages(thread_id)
        checked = 0
        for msg in messages:
            if msg.role.value != "user":
                continue
            if msg.payload and msg.payload.get("is_internal"):
                continue
            if _INTENT_SIGNAL_RE.search(msg.content):
                logger.debug(
                    "Intent signal detected early in thread %s (msg %s)",
                    thread_id, msg.id,
                )
                return True
            checked += 1
            if checked >= 3:  # On se limite aux 3 premiers messages user
                break
        return False

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

        # Minimum 2 lignes (1 user + 1 assistant) pour une extraction pertinente.
        # L'extraction précoce (niveau 1) peut s'opérer dès le premier échange
        # quand l'utilisateur a exprimé une intention claire.
        if len(conversation_lines) < 2:
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
