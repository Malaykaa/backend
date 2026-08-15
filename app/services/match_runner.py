"""MatchRunner — job périodique : matching offres → users avec intention active.

Pour chaque user éligible :
1. Charge sa meilleure intention (UserIntent la plus récente).
2. Embed l'intention via EmbeddingService (recherche sémantique).
3. Recherche les top-K offres scrapées via ScrapedOfferService.
4. Pousse un message assistant dans le thread de l'intention.
5. Envoie une notif WhatsApp (OneMessage / Twilio) si phone + opt-in.
6. Met à jour profile.match_last_run_at.

Critères d'éligibilité (cf. _eligible_user_ids) :
- Profile.match_notifications_enabled = TRUE
- Au moins une UserIntent extraite < 30 jours
- match_last_run_at + match_frequency_hours <= now (ou NULL)

Le service est utilisé par scheduler.py et expose run_once() pour le run
manuel via l'admin / les tests.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.chat import MessageRole
from app.models.user import Profile, User
from app.models.user_intent import UserIntent
from app.repositories.chat_repo import ChatRepository
from app.repositories.feedback_repo import FeedbackRepository
from app.services.scraped_offer_service import (
    MATCH_MODE_HYBRID,
    MATCH_MODE_LEXICAL,
    MATCH_MODE_SEMANTIC,
    ScrapedOfferService,
)
from app.services.whatsapp_service import whatsapp_service

if TYPE_CHECKING:
    from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Une intention plus ancienne que ça est considérée stale → on n'envoie plus
# de matching automatique tant que l'utilisateur n'a pas remis à jour son
# objectif (évite de spammer pour des intentions périmées).
INTENT_MAX_AGE_DAYS = 30

# Seuil de notification in-app, exprimé sur l'échelle commune `match_score`
# (0–100). Dépend du mode : le repli lexical dispose d'un signal plus pauvre
# que le sémantique, exiger de lui la même confiance le rendrait muet.
NOTIFY_THRESHOLD: dict[str, float] = {
    MATCH_MODE_SEMANTIC: 80.0,
    MATCH_MODE_LEXICAL: 70.0,
    # Les deux voies ont trouvé l'offre indépendamment. C'est la corroboration
    # la plus forte dont on dispose, on peut donc se permettre d'être un peu
    # moins exigeant qu'en sémantique seul sans notifier du bruit.
    MATCH_MODE_HYBRID: 75.0,
}
NOTIFY_THRESHOLD_DEFAULT = 80.0


# Nombre maximum d'objectifs traités par run. Au-delà, le quota par objectif
# tomberait à une offre et le message perdrait sa substance.
MAX_GOALS_PER_RUN = 3

# Amplitude maximale, en points de `match_score`, de l'ajustement lié au goût
# exprimé par l'utilisateur. Volontairement modeste : le feedback affine un
# classement, il ne doit jamais se substituer à la pertinence mesurée ni
# enfermer quelqu'un dans ce qu'il a déjà consulté.
AFFINITY_MAX_ADJUSTMENT = 12.0


def _apply_type_affinity(offers: list[dict], affinity: dict[str, float]) -> None:
    """Module `match_score` selon le goût de l'utilisateur, sur place.

    `affinity` associe un type d'offre à une valeur dans [-1, +1], dérivée des
    actions passées (postulé, sauvegardé, cliqué / ignoré, écarté).

    Jusqu'ici ce signal n'était exploité que par le router de recommandations,
    et uniquement sur l'offre exacte : écarter dix offres d'un type n'empêchait
    pas d'en recevoir une onzième par WhatsApp.

    L'ajustement est borné et le score reste dans [0, 100]. Un type jamais
    évalué laisse l'offre inchangée.
    """
    if not affinity:
        return
    for offer in offers:
        weight = affinity.get(offer.get("type") or "")
        if not weight:
            continue
        adjusted = _match_score_of(offer) + AFFINITY_MAX_ADJUSTMENT * weight
        offer["match_score"] = max(0.0, min(100.0, adjusted))
        offer["affinity_applied"] = round(weight, 3)


def _log_match_batch(
    user_id: uuid.UUID, intent: UserIntent, offers: list[dict]
) -> None:
    """Trace ce qui est envoyé, pour pouvoir diagnostiquer une plainte.

    Sans cette trace, une réclamation « je reçois n'importe quoi » était
    ininstruisable : ni le mode de recherche, ni le score, ni le rang n'étaient
    conservés. On journalise volontairement le mode par offre — c'est lui qui
    dit si le résultat vient du vecteur, des mots-clés, ou des deux.
    """
    detail = " | ".join(
        f"#{rank} {o.get('match_mode', '?')} {_match_score_of(o):.0f}% "
        f"{(o.get('title') or '')[:40]}"
        for rank, o in enumerate(offers, start=1)
    )
    logger.info(
        "[Match] user=%s goal=%s intent_type=%s domaine=%s → %d offre(s) : %s",
        user_id, intent.goal_id, intent.intent_type, intent.domain,
        len(offers), detail,
    )


def _log_llm_filter(
    user_id: uuid.UUID, intent: UserIntent, before: int, after: int
) -> None:
    """Compte les offres écartées par la vérification LLM.

    Un taux d'exclusion élevé est le signal le plus précoce qu'un matching
    part en vrille : les candidats remontent, mais le LLM les juge hors sujet.
    Auparavant seul le rejet total laissait une trace.
    """
    excluded = before - after
    if excluded <= 0:
        return
    logger.info(
        "[Match] user=%s goal=%s : %d/%d offre(s) écartée(s) par le LLM",
        user_id, intent.goal_id, excluded, before,
    )


def _match_score_of(offer: dict) -> float:
    """Lit `match_score` en tolérant les dicts produits avant son introduction.

    Un dict sans `match_score` provient forcément du chemin sémantique
    historique, dont le score brut vivait sur 0–75 : on le reprojette pour ne
    pas régresser silencieusement à 0 si un appelant construit encore des
    offres à l'ancienne.
    """
    value = offer.get("match_score")
    if value is not None:
        return float(value)
    legacy = float(offer.get("relevance_score") or 0.0)
    return min(100.0, legacy / 75.0 * 100.0)


class MatchRunner:
    def __init__(self, db: Session, llm: "LLMProvider | None" = None) -> None:
        self.db = db
        self.llm = llm
        self.settings = get_settings()
        self.offer_svc = ScrapedOfferService(db)
        self.chat_repo = ChatRepository(db)
        self.feedback_repo = FeedbackRepository(db)

    async def run_once(self) -> dict[str, int]:
        """Exécute un cycle complet. Retourne des stats pour le scheduler."""
        stats = {"eligible": 0, "matched": 0, "notified": 0, "errors": 0}
        now = datetime.now(timezone.utc)

        for user_id in self._eligible_user_ids(now):
            stats["eligible"] += 1
            try:
                pushed = await self._run_for_user(user_id, now)
                if pushed > 0:
                    stats["matched"] += 1
                    stats["notified"] += pushed
            except Exception:
                stats["errors"] += 1
                logger.exception("MatchRunner failed for user %s", user_id)
                self.db.rollback()

        logger.info("MatchRunner: %s", stats)
        return stats

    # ── Privé ──────────────────────────────────────────────────────────────────

    def _eligible_user_ids(self, now: datetime) -> list[uuid.UUID]:
        """Calcule la liste des users à traiter à ce tick.

        Évalue la fréquence personnalisée côté Python pour ne pas dupliquer
        la logique en SQL : la table users est suffisamment petite pour que
        ce soit OK ; passer à un index couvrant si la base grossit.
        """
        intent_cutoff = now - timedelta(days=INTENT_MAX_AGE_DAYS)
        default_freq = self.settings.match_default_frequency_hours

        # Sous-requête : ids des users avec au moins une intention récente.
        users_with_intent = (
            select(UserIntent.user_id)
            .where(UserIntent.extracted_at >= intent_cutoff)
            .distinct()
            .subquery()
        )
        stmt = (
            select(
                User.id,
                Profile.match_frequency_hours,
                Profile.match_last_run_at,
            )
            .join(Profile, Profile.user_id == User.id)
            .join(users_with_intent, users_with_intent.c.user_id == User.id)
            .where(
                User.is_active.is_(True),
                Profile.match_notifications_enabled.is_(True),
            )
        )

        eligible: list[uuid.UUID] = []
        for user_id, freq, last_run in self.db.execute(stmt).all():
            interval = timedelta(hours=freq if freq else default_freq)
            if last_run is None or (now - last_run) >= interval:
                eligible.append(user_id)
        return eligible

    async def _run_for_user(self, user_id: uuid.UUID, now: datetime) -> int:
        """Exécute le matching pour un user. Retourne le nb d'offres poussées.

        Traite **toutes** les intentions actives, pas seulement la plus
        récente : un utilisateur suivant trois objectifs en avait deux
        silencieusement privés de matching.

        Le volume total reste borné par `match_top_k` — le quota est réparti
        entre les objectifs, chacun en recevant au moins un. Couvrir plus
        d'objectifs ne doit pas se traduire par plus de sollicitations.
        """
        intents = self._active_intents(user_id)
        if not intents:
            return 0

        # Chaque objectif porte sa propre fenêtre horaire (notif_mode /
        # notif_time du goal) : on filtre avant de répartir le quota.
        due = [i for i in intents if self._is_scheduled_time(i, now)]
        if not due:
            return 0

        already_sent = self.feedback_repo.get_sent_offer_refs(user_id)
        affinity = self.feedback_repo.get_type_affinity(user_id)
        quota = max(1, self.settings.match_top_k // len(due))

        batches: list[tuple[UserIntent, list[dict]]] = []
        seen_refs: set[str] = set()

        for intent in due:
            # On élargit (x3) car les offres déjà envoyées seront exclues
            # ensuite — éviter de retomber sous le quota après filtrage.
            found = await self.offer_svc.search_for_matching(
                intent, limit=quota * 3,
            )
            if not found:
                continue

            # Exclure les offres déjà poussées à ce user (action=sent), et
            # celles déjà retenues pour un autre objectif de ce même run.
            candidates = [
                o for o in found
                if o.get("offer_ref") not in already_sent
                and o.get("offer_ref") not in seen_refs
            ]
            if not candidates:
                continue

            # Le goût exprimé par l'utilisateur module la confiance avant
            # la sélection finale.
            _apply_type_affinity(candidates, affinity)
            candidates.sort(key=_match_score_of, reverse=True)
            selected = candidates[:quota]

            # Vérification LLM de pertinence avant présentation.
            # Si self.llm est None (scheduler non configuré), on passe.
            if self.llm is not None:
                before = len(selected)
                selected = await _verify_relevance_llm(selected, intent, self.llm)
                _log_llm_filter(user_id, intent, before, len(selected))
            if not selected:
                continue

            seen_refs.update(o["offer_ref"] for o in selected if o.get("offer_ref"))
            batches.append((intent, selected))

        if not batches:
            self._mark_run(user_id, now)
            self.db.commit()
            return 0

        all_offers: list[dict] = []
        for intent, offers in batches:
            _log_match_batch(user_id, intent, offers)

            # 1. Message assistant dans le thread de l'objectif concerné.
            interaction = _build_interaction_payload(offers)
            self.chat_repo.add_message(
                thread_id=intent.thread_id,
                role=MessageRole.assistant,
                content=_format_chat_message(offers),
                payload={
                "kind": "auto_match",
                "offers": [o["offer_ref"] for o in offers],
                # inject_markers (appelé sur fetch) transforme ces champs
                # en @@PROPOSITIONS@@ et @@STEPS@@ — contextualisés selon
                # le type dominant des offres matchées.
                    "suggestions": interaction["suggestions"],
                    "steps": interaction["steps"],
                },
            )
            all_offers.extend(offers)

        # 2. Notifications in-app pour les offres à haute pertinence.
        self._create_match_notifications(user_id, all_offers)

        # Marquer ces offres comme envoyées — ne plus jamais les repousser
        # à ce user, même si elles restent pertinentes lors d'un run futur.
        self.feedback_repo.mark_sent_batch(
            user_id, [o["offer_ref"] for o in all_offers if o.get("offer_ref")]
        )

        # 3. Une seule notification WhatsApp par run, quel que soit le nombre
        #    d'objectifs traités : plusieurs messages coup sur coup seraient
        #    vécus comme du spam. Le lien pointe vers l'objectif le mieux servi.
        phone = self._user_phone(user_id)
        if phone:
            richest = max(batches, key=lambda b: len(b[1]))[0]
            try:
                thread_url = f"{self.settings.frontend_url.rstrip('/')}/app/chat/{richest.thread_id}"
                await whatsapp_service.send_message(
                    phone,
                    _format_wa_message(all_offers, thread_url),
                )
            except Exception:
                logger.warning(
                    "WhatsApp notification failed for user %s — chat message kept",
                    user_id, exc_info=True,
                )
                # On ne raise pas : le message en chat est déjà posé.

        self._mark_run(user_id, now)
        self.db.commit()
        return len(all_offers)

    def _active_intents(self, user_id: uuid.UUID) -> list[UserIntent]:
        """Une intention par objectif actif, la plus récente de chacun.

        `_best_intent` ne renvoyait que la dernière intention tous objectifs
        confondus : les autres objectifs de l'utilisateur ne recevaient jamais
        de matching, sans que rien ne le signale.

        Les intentions sans `goal_id` (extraites d'un thread libre) sont
        regroupées sous une clé commune : elles décrivent la même recherche
        non rattachée, inutile de les traiter plusieurs fois.

        Le nombre d'objectifs traités par run est borné pour que le quota par
        objectif reste utile — au-delà, chacun recevrait une seule offre.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=INTENT_MAX_AGE_DAYS)
        rows = self.db.execute(
            select(UserIntent)
            .where(
                UserIntent.user_id == user_id,
                UserIntent.extracted_at >= cutoff,
            )
            .order_by(UserIntent.extracted_at.desc())
        ).scalars().all()

        latest_per_goal: dict[str, UserIntent] = {}
        for intent in rows:  # déjà triées du plus récent au plus ancien
            key = str(intent.goal_id) if intent.goal_id else "_no_goal"
            if key not in latest_per_goal:
                latest_per_goal[key] = intent
        return list(latest_per_goal.values())[:MAX_GOALS_PER_RUN]

    def _best_intent(self, user_id: uuid.UUID) -> UserIntent | None:
        """Dernière intention assez récente pour servir de query de matching."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=INTENT_MAX_AGE_DAYS)
        return self.db.execute(
            select(UserIntent)
            .where(
                UserIntent.user_id == user_id,
                UserIntent.extracted_at >= cutoff,
            )
            .order_by(UserIntent.extracted_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _user_phone(self, user_id: uuid.UUID) -> str | None:
        return self.db.execute(
            select(User.phone).where(User.id == user_id)
        ).scalar_one_or_none()

    def _create_match_notifications(
        self, user_id: uuid.UUID, offers: list[dict]
    ) -> None:
        """Crée des notifications in-app pour les offres à haute pertinence.

        S'appuie sur `match_score`, normalisé 0–100 et comparable entre les deux
        modes de recherche. L'ancienne version lisait `relevance_score`, dont
        l'échelle diffère selon le mode : en repli lexical le maximum atteignable
        était 25 pour un seuil fixé à 60, donc aucune notification ne pouvait
        jamais être créée.

        Le seuil dépend du mode. Le lexical porte moins d'information que le
        sémantique — exiger la même confiance des deux reviendrait à le rendre
        muet, l'exiger trop peu reviendrait à notifier du bruit.
        """
        from app.models.notification import UserNotification

        created = 0
        for o in offers:
            match_score = _match_score_of(o)
            mode = o.get("match_mode") or MATCH_MODE_SEMANTIC
            if match_score < NOTIFY_THRESHOLD.get(mode, NOTIFY_THRESHOLD_DEFAULT):
                continue
            score_pct = min(99, round(match_score))
            offer_id_raw = o.get("id") or o.get("offer_id")
            try:
                offer_id = uuid.UUID(str(offer_id_raw)) if offer_id_raw else None
            except ValueError:
                offer_id = None

            notif = UserNotification(
                user_id=user_id,
                offer_id=offer_id,
                offer_title=(o.get("title") or "")[:500] or None,
                offer_url=(o.get("url") or "")[:2048] or None,
                offer_type=o.get("type") or None,
                score_pct=score_pct,
                seen=False,
            )
            self.db.add(notif)
            created += 1

        if created:
            logger.info(
                "MatchRunner: %d notification(s) haute-pertinence créée(s) pour user %s",
                created, user_id,
            )
            # Un seul push résumant le lot plutôt qu'un par offre : les
            # meilleures correspondances sont déjà peu nombreuses
            # (`match_top_k`), mais en envoyer une par notification
            # spammerait l'utilisateur pour ce qui reste un seul événement
            # de son point de vue — « de nouvelles offres sont arrivées ».
            from app.services.push_service import send_push
            title = (
                "Une nouvelle offre vous correspond" if created == 1
                else f"{created} nouvelles offres vous correspondent"
            )
            send_push(self.db, user_id=user_id, title=title, url="/app/pour-moi")

    def _mark_run(self, user_id: uuid.UUID, now: datetime) -> None:
        self.db.execute(
            update(Profile)
            .where(Profile.user_id == user_id)
            .values(match_last_run_at=now)
        )

    def _is_scheduled_time(self, intent: UserIntent, now: datetime) -> bool:
        """Vérifie si l'heure actuelle (UTC) correspond à la fenêtre de notification.

        Logique :
        - Pas de goal_id → True  (pas de contrainte horaire)
        - notif_mode absent ou "realtime" → True  (comportement existant inchangé)
        - notif_mode == "scheduled" + notif_time "HH:MM" → True uniquement si
          now.hour (UTC) == heure programmée.

        Le scheduler tourne à :30 de chaque heure. Un utilisateur qui programme
        "09:00" sera notifié à 09h30 UTC (même heure, tick de :30).

        En cas d'erreur de parsing → True (ne pas bloquer silencieusement).
        """
        if intent.goal_id is None:
            return True

        from app.models.goal import Goal
        goal = self.db.get(Goal, intent.goal_id)
        if goal is None:
            return True

        ctx = goal.context_data or {}
        notif_mode = ctx.get("notif_mode", "realtime")
        notif_time = ctx.get("notif_time")  # format attendu : "HH:MM"

        if notif_mode != "scheduled" or not notif_time:
            return True  # mode realtime → pas de contrainte horaire

        try:
            target_hour = int(str(notif_time).split(":")[0])
        except (ValueError, IndexError):
            logger.warning(
                "[MatchRunner] notif_time invalide '%s' pour goal %s — traitement immédiat",
                notif_time, intent.goal_id,
            )
            return True  # format inattendu → ne pas bloquer

        current_hour = now.hour  # UTC, cohérent avec le scheduler
        if current_hour != target_hour:
            logger.debug(
                "[MatchRunner] user %s : heure programmée %dh, heure courante %dh UTC — skip",
                intent.user_id, target_hour, current_hour,
            )
            return False

        return True


# ── Vérification LLM de pertinence ─────────────────────────────────────────

_VERIFY_SYSTEM = (
    "Tu évalues la pertinence d'offres d'opportunités par rapport à l'intention d'un utilisateur.\n"
    "Réponds UNIQUEMENT en JSON valide, sans texte autour.\n"
    "Format strict : {\"pertinentes\": [numéros], \"partielles\": [numéros], \"hors_sujet\": [numéros]}\n\n"
    "Critères :\n"
    "- pertinentes  : offre correspond bien (domaine, type, localisation cohérents)\n"
    "- partielles   : offre liée au domaine mais décalée (mauvais niveau, localisation éloignée, type différent)\n"
    "- hors_sujet   : offre sans rapport réel avec l'intention\n\n"
    "Règle : chaque numéro doit apparaître exactement une fois au total."
)


async def _verify_relevance_llm(
    offers: list[dict],
    intent: "UserIntent",
    llm: "LLMProvider",
) -> list[dict]:
    """Filtre et reordonne les offres via un appel LLM léger (temperature=0).

    Logique :
    - pertinentes en premier, partielles en second, hors_sujet supprimées.
    - Si le LLM rejette tout → fallback silencieux sur la liste pgvector originale.
    - Si le LLM échoue (erreur réseau, JSON invalide) → même fallback.

    Coût : ~300-400 tokens en entrée + ~80 tokens en sortie par run utilisateur.
    """
    import json as _json

    # ── Contexte intention ──────────────────────────────────────────────────
    ctx_parts = [f"Intention : {intent.intent_summary}"]
    details = []
    if intent.domain:
        details.append(f"domaine={intent.domain}")
    if intent.level:
        details.append(f"niveau={intent.level}")
    if intent.location:
        details.append(f"localisation={intent.location}")
    if intent.intent_type:
        details.append(f"type={intent.intent_type}")
    if details:
        ctx_parts.append(" | ".join(details))

    # ── Liste numérotée des offres (1-based, descriptions courtes) ──────────
    offer_lines: list[str] = []
    for i, o in enumerate(offers, start=1):
        title = (o.get("title") or "")[:100]
        loc   = (o.get("location") or "")
        desc  = (o.get("description") or "")[:120]
        otype = (o.get("type") or "")
        line  = f"[{i}] {title}"
        if otype:
            line += f" ({otype})"
        if loc:
            line += f" — {loc}"
        if desc:
            line += f" — {desc}"
        offer_lines.append(line)

    user_content = (
        "\n".join(ctx_parts)
        + "\n\nOffres candidates :\n"
        + "\n".join(offer_lines)
    )

    messages = [
        {"role": "system", "content": _VERIFY_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    try:
        raw = await llm.complete(messages, temperature=0.0, max_tokens=150)

        # Nettoyer les éventuels blocs markdown
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])

        data = _json.loads(cleaned.strip())
        pertinentes = [int(x) for x in data.get("pertinentes", []) if str(x).isdigit()]
        partielles  = [int(x) for x in data.get("partielles",  []) if str(x).isdigit()]

        # Construire la liste finale : pertinentes d'abord, partielles ensuite
        kept: list[int] = []
        for idx in pertinentes + partielles:
            if 1 <= idx <= len(offers) and idx not in kept:
                kept.append(idx)

        if not kept:
            # LLM a tout mis en hors_sujet → on garde les résultats pgvector
            # (mieux vaut montrer quelque chose que rien)
            logger.warning(
                "[MatchRunner] LLM verification rejected all %d offers — keeping originals",
                len(offers),
            )
            return offers

        filtered = [offers[i - 1] for i in kept]
        excluded = len(offers) - len(filtered)
        if excluded:
            logger.info(
                "[MatchRunner] LLM verification: %d/%d offres retenues (%d hors_sujet supprimées)",
                len(filtered), len(offers), excluded,
            )
        return filtered

    except Exception:
        logger.warning(
            "[MatchRunner] LLM verification failed — keeping original pgvector results",
            exc_info=True,
        )
        return offers  # Fallback silencieux : pgvector reste la ligne de base


# ── Interaction contextuelle (steps + propositions) ────────────────────────

# Mapping offer_type → (suggestions, steps).
# Couvre les types définis dans ScrapedOffer.offer_type et les intents courants.
# Si le type est absent ou inconnu → fallback générique.
_OFFER_TYPE_INTERACTIONS: dict[str, tuple[list[str], list[dict]]] = {
    "job": (
        [
            "Rédiger mon CV pour cette offre",
            "Préparer ma lettre de motivation",
            "Analyser les compétences requises",
        ],
        [
            {"id": "s1", "label": "Étudier l'offre d'emploi", "description": "Lisez attentivement les critères, le niveau requis et les responsabilités du poste.", "order": 1},
            {"id": "s2", "label": "Mettre à jour mon CV", "description": "Adaptez votre CV aux exigences spécifiques du poste.", "order": 2},
            {"id": "s3", "label": "Rédiger la lettre de motivation", "description": "Rédigez une lettre ciblée qui met en valeur votre adéquation avec le poste.", "order": 3},
            {"id": "s4", "label": "Envoyer ma candidature", "description": "Soumettez votre dossier complet avant la date limite.", "order": 4},
        ],
    ),
    "scholarship": (
        [
            "Vérifier mon éligibilité à la bourse",
            "Préparer mon dossier de candidature",
            "Rédiger ma lettre de motivation",
        ],
        [
            {"id": "s1", "label": "Vérifier l'éligibilité", "description": "Consultez les critères (nationalité, niveau d'études, domaine) et confirmez que vous y répondez.", "order": 1},
            {"id": "s2", "label": "Rassembler les documents", "description": "Relevés de notes, lettres de recommandation, CV académique et pièce d'identité.", "order": 2},
            {"id": "s3", "label": "Rédiger la lettre de motivation", "description": "Expliquez votre projet académique, vos objectifs et pourquoi vous méritez cette bourse.", "order": 3},
            {"id": "s4", "label": "Soumettre le dossier", "description": "Déposez votre candidature complète avant la deadline officielle.", "order": 4},
        ],
    ),
    "grant": (
        [
            "Évaluer l'éligibilité de mon projet",
            "Préparer mon dossier de financement",
            "Rédiger le pitch de mon projet",
        ],
        [
            {"id": "s1", "label": "Analyser les critères du financement", "description": "Secteur cible, montant disponible, conditions d'éligibilité et documents requis.", "order": 1},
            {"id": "s2", "label": "Structurer le projet", "description": "Définissez clairement les objectifs, le budget prévisionnel et l'impact attendu.", "order": 3},
            {"id": "s3", "label": "Monter le dossier", "description": "Préparez le business plan, les états financiers et les justificatifs demandés.", "order": 3},
            {"id": "s4", "label": "Soumettre la demande", "description": "Déposez le dossier complet avant la clôture de l'appel.", "order": 4},
        ],
    ),
    "call_for_applications": (
        [
            "Analyser le cahier des charges",
            "Vérifier les critères de participation",
            "Préparer mon dossier de réponse",
        ],
        [
            {"id": "s1", "label": "Lire le cahier des charges", "description": "Comprenez les exigences techniques, financières et administratives de l'appel.", "order": 1},
            {"id": "s2", "label": "Vérifier l'éligibilité", "description": "Assurez-vous que votre profil ou structure correspond aux critères de participation.", "order": 2},
            {"id": "s3", "label": "Préparer l'offre technique", "description": "Rédigez la proposition technique détaillée en réponse aux exigences.", "order": 3},
            {"id": "s4", "label": "Soumettre le dossier complet", "description": "Envoyez l'ensemble des documents requis avant la date de clôture.", "order": 4},
        ],
    ),
    "formation": (
        [
            "Vérifier les prérequis de la formation",
            "Comparer avec d'autres formations similaires",
            "M'inscrire à la formation",
        ],
        [
            {"id": "s1", "label": "Étudier le programme", "description": "Consultez le contenu, la durée, le format (présentiel/distanciel) et les prérequis.", "order": 1},
            {"id": "s2", "label": "Vérifier l'éligibilité", "description": "Assurez-vous de remplir les conditions d'admission.", "order": 2},
            {"id": "s3", "label": "Préparer le dossier d'inscription", "description": "Rassemblez les documents requis (CV, diplômes, lettre de motivation si nécessaire).", "order": 3},
            {"id": "s4", "label": "Soumettre la candidature", "description": "Déposez votre inscription avant la date limite.", "order": 4},
        ],
    ),
    "partnership": (
        [
            "Analyser les conditions du partenariat",
            "Préparer une proposition de collaboration",
            "Contacter le porteur du projet",
        ],
        [
            {"id": "s1", "label": "Comprendre les termes", "description": "Lisez les conditions du partenariat : responsabilités, engagements et bénéfices attendus.", "order": 1},
            {"id": "s2", "label": "Évaluer la compatibilité", "description": "Vérifiez que vos objectifs et ressources correspondent à ceux du partenaire.", "order": 2},
            {"id": "s3", "label": "Rédiger la proposition", "description": "Préparez une lettre ou présentation expliquant votre intérêt et votre valeur ajoutée.", "order": 3},
            {"id": "s4", "label": "Engager la collaboration", "description": "Contactez le porteur du projet et formalisez l'accord.", "order": 4},
        ],
    ),
}

# Fallback si aucun type connu (opportunity, resource, inconnu…)
_DEFAULT_INTERACTIONS: tuple[list[str], list[dict]] = (
    [
        "M'aider à préparer ma candidature",
        "Analyser les critères de cette opportunité",
        "Affiner mes préférences de matching",
    ],
    [
        {"id": "s1", "label": "Étudier l'opportunité", "description": "Lisez attentivement les détails et critères de l'opportunité.", "order": 1},
        {"id": "s2", "label": "Préparer votre dossier", "description": "Rassemblez les documents nécessaires selon les exigences.", "order": 2},
        {"id": "s3", "label": "Soumettre votre candidature", "description": "Postulez via le lien de l'offre avant la date limite.", "order": 3},
    ],
)


def _build_interaction_payload(offers: list[dict]) -> dict:
    """Dérive suggestions et steps du type dominant des offres matchées.

    Stratégie : type le plus fréquent dans la liste. En cas d'égalité,
    on prend le premier rencontré (ordre déjà trié par score décroissant).
    """
    from collections import Counter
    types = [o.get("type") for o in offers if o.get("type")]
    dominant = Counter(types).most_common(1)[0][0] if types else None
    suggestions, steps = _OFFER_TYPE_INTERACTIONS.get(
        dominant or "", _DEFAULT_INTERACTIONS
    )
    return {"suggestions": suggestions, "steps": steps}


# ── Formatage des messages ──────────────────────────────────────────────────


def _format_chat_message(offers: Iterable[dict]) -> str:
    """Message assistant en markdown — listing enrichi avec score, description, liens."""
    lines = [
        "Voici de nouvelles opportunités qui correspondent à votre objectif :",
        "",
    ]
    for i, o in enumerate(offers, start=1):
        title = o.get("title") or "(sans titre)"
        url = o.get("url") or ""
        company = o.get("company") or ""
        location = o.get("location") or ""
        description = (o.get("description") or "").strip()
        # `match_score` est déjà sur 0–100 et comparable entre modes ; l'ancien
        # calcul divisait `relevance_score` par 75, ce qui plafonnait l'affichage
        # à 33 % en repli lexical où le maximum réel est 25.
        match_pct = min(99, round(_match_score_of(o)))

        link = f"[{title}]({url})" if url else f"**{title}**"
        meta = " · ".join(p for p in [company, location] if p)

        lines.append(f"**{i}. {link}**")
        pct_line = f"*Pertinence estimée : {match_pct} %*"
        if meta:
            pct_line += f" · {meta}"
        lines.append(pct_line)
        if description:
            preview = description[:200] + "…" if len(description) > 200 else description
            lines.append(preview)
        lines.append("")

    lines.append(
        "_Cette sélection est mise à jour automatiquement selon votre intention. "
        "Vous pouvez ajuster la fréquence ou désactiver ces notifications dans vos préférences._"
    )
    return "\n".join(lines)


def _format_wa_message(offers: Iterable[dict], thread_url: str) -> str:
    """Notif WhatsApp courte — renvoie vers le thread Malayka, pas vers les offres directement."""
    items = list(offers)
    lines = [f"Malayka — {len(items)} nouvelle(s) opportunité(s) pour vous :"]
    for o in items:
        title = (o.get("title") or "").strip()
        if len(title) > 80:
            title = title[:77] + "..."
        lines.append(f"• {title}")
    lines.append("")
    lines.append("Ouvrez votre discussion sur Malayka pour voir les détails et postuler :")
    lines.append(thread_url)
    return "\n".join(lines)
