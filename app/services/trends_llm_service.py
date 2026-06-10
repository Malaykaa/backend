"""Interprétations LLM des tendances marché — personnalisées par profil utilisateur."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time

import redis as redis_lib

from app.core.config import get_settings
from app.llm import get_llm_provider

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 2 * 3600  # 2h — réduit la staleness après ajout massif d'offres
_CACHE_KEY_PREFIX  = "trends:interp:v3:"
_REDIS_COOLDOWN    = 30

_redis_client: redis_lib.Redis | None = None
_redis_unavailable_until: float = 0.0


def _redis() -> redis_lib.Redis | None:
    global _redis_client, _redis_unavailable_until
    if _redis_client is not None:
        return _redis_client
    now = time.monotonic()
    if _redis_unavailable_until > now:
        return None
    settings = get_settings()
    if not settings.redis_url:
        _redis_unavailable_until = now + _REDIS_COOLDOWN
        return None
    try:
        client = redis_lib.from_url(
            settings.redis_url, decode_responses=True,
            socket_timeout=2, socket_connect_timeout=2,
        )
        client.ping()
        _redis_client = client
        _redis_unavailable_until = 0.0
        return _redis_client
    except Exception as exc:
        _redis_unavailable_until = now + _REDIS_COOLDOWN
        logger.warning("TrendsLLM cache: Redis inaccessible — %s", exc)
        return None


def _cache_key(raw: dict, user_context: dict) -> str:
    """Clé par utilisateur + données marché.

    Deux utilisateurs avec le même profil mais des données marché différentes
    obtiennent des clés différentes. Un utilisateur qui recharge la page dans
    les 2h obtient le même résultat depuis le cache.
    """
    mon_pays = raw.get("mon_pays", {})
    market_fingerprint = {
        "tendances": [(t["type"], t["count"]) for t in raw.get("week_africa", {}).get("tendances", [])],
        "total":     raw.get("vue_globale", {}).get("total_offres", 0),
        # par_type inclus pour détecter les changements de répartition même si le total est stable
        "par_type":  sorted(raw.get("vue_globale", {}).get("par_type", {}).items()),
        "pays":      mon_pays.get("pays", ""),
        # données pays incluses car elles changent indépendamment du total africain
        "pays_counts": (
            mon_pays.get("emplois", 0),
            mon_pays.get("financements", 0),
            mon_pays.get("missions", 0),
            mon_pays.get("bourses", 0),
        ),
        # pct_offres inclus : même top-10 noms mais % différents → clé différente
        "comps": [(c["competence"], round(c.get("pct_offres", 0), 1)) for c in raw.get("competences", [])],
    }
    user_fingerprint = {
        "user_id":      user_context.get("user_id", ""),
        "goals":        sorted(user_context.get("active_goals", [])),
        "skills":       sorted(user_context.get("skills") or []),
        "domain":       user_context.get("domain", ""),
        "intent_types": sorted(user_context.get("intent_types") or []),
    }
    payload = json.dumps(
        {"market": market_fingerprint, "user": user_fingerprint},
        sort_keys=True,
    )
    return _CACHE_KEY_PREFIX + hashlib.sha256(payload.encode()).hexdigest()[:20]


_SYSTEM = """Tu es un conseiller expert des marchés africains — emploi, financement, bourses, missions freelance.
Tu reçois des données de tendances hebdomadaires et le profil complet d'un utilisateur.
Tu produis des interprétations PERSONNALISÉES : tu t'adresses à cette personne spécifiquement,
tu nommes ses objectifs, tu lui dis ce que les données signifient POUR SON PARCOURS.

Règles absolues :
- Réponse en JSON valide UNIQUEMENT — aucun texte avant ou après, aucun bloc markdown
- "tu" direct — jamais "vous", jamais de généralités
- Chaque phrase doit référencer explicitement l'objectif ou le domaine de l'utilisateur
- Si l'utilisateur cherche une bourse : parle de bourses, pas d'emplois
- Si l'utilisateur est développeur : cite des compétences tech, pas du marketing
- 2 à 4 phrases par champ maximum — concis et actionnable
- Langue : français
- CRITIQUE — cohérence des chiffres : tu peux mentionner les chiffres BRUTS des données marché
  (ex: "145 offres d'emploi publiées cette semaine") car ils viennent directement du JSON.
  NE génère JAMAIS de chiffres filtrés inventés dans les champs action/ce_qui_se_passe
  (ex: NE DIS PAS "voir les 5 offres en remote" ou "80 offres en communication" car ces filtres
  ne correspondent à aucun résultat réel vérifiable). Les boutons de navigation vers les offres
  sont générés automatiquement par l'interface — ton rôle est uniquement l'interprétation qualitative."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        return json.loads(match.group(1).strip())
    raise ValueError("Réponse LLM sans JSON valide")


class TrendsLLMService:
    """Génère toutes les interprétations de tendances en un seul appel LLM, par utilisateur."""

    def __init__(self) -> None:
        self._provider = get_llm_provider()

    async def interpret_all(self, raw: dict, user_context: dict | None = None) -> dict | None:
        """
        Un appel LLM → interprétations personnalisées pour les 4 sections.
        Résultat mis en cache Redis 4h par utilisateur. Retourne None si LLM inaccessible.
        """
        ctx = user_context or {}
        key = _cache_key(raw, ctx)
        r = _redis()

        # Lecture cache
        if r is not None:
            try:
                cached = r.get(key)
                if cached:
                    logger.debug("TrendsLLM: cache hit — %s", key)
                    return json.loads(cached)
            except Exception as exc:
                logger.warning("TrendsLLM: erreur lecture cache — %s", exc)

        # Appel LLM
        try:
            prompt = _build_prompt(raw, ctx)
            response = await self._provider.complete(
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2800,
            )
            result = _extract_json(response)
        except Exception as exc:
            logger.warning("TrendsLLMService: échec interprétation — %s", exc)
            return None

        # Écriture cache
        if r is not None and result:
            try:
                r.setex(key, _CACHE_TTL_SECONDS, json.dumps(result))
                logger.debug("TrendsLLM: cache write — %s (TTL %ds)", key, _CACHE_TTL_SECONDS)
            except Exception as exc:
                logger.warning("TrendsLLM: erreur écriture cache — %s", exc)

        return result


# ── Construction du prompt personnalisé ──────────────────────────────────────

def _fmt_skills(skills) -> str:
    if not skills:
        return "non renseignées"
    if isinstance(skills, list):
        return ", ".join(str(s) for s in skills[:10]) or "non renseignées"
    return str(skills)


def _fmt_goals(goals: list[str]) -> str:
    if not goals:
        return "non définis"
    return " / ".join(goals)


def _build_prompt(raw: dict, ctx: dict) -> str:
    week        = raw.get("week_africa", {})
    mon_pays    = raw.get("mon_pays", {})
    competences = raw.get("competences", [])
    globale     = raw.get("vue_globale", {})
    signal      = globale.get("signal_semaine")

    # ── Bloc profil utilisateur ───────────────────────────────────────────────
    role             = ctx.get("primary_role") or "non précisé"
    intent_domains   = ctx.get("intent_domains") or []
    domain           = ctx.get("domain") or (intent_domains[0] if intent_domains else "") or "non précisé"
    field            = ctx.get("field_of_study") or ""
    status           = ctx.get("current_status") or ""
    country          = ctx.get("country") or mon_pays.get("pays", "Afrique")
    skills           = _fmt_skills(ctx.get("skills"))
    goals            = _fmt_goals(ctx.get("active_goals", []))
    intent_summaries = ctx.get("intent_summaries") or []
    intent_types     = ctx.get("intent_types") or []
    keywords         = ctx.get("intent_keywords") or []
    keywords_str     = ", ".join(str(k) for k in keywords[:8]) if keywords else ""

    profile_block = f"""=== PROFIL DE L'UTILISATEUR ===
Rôle / poste : {role}
Domaine : {domain}{f' — {field}' if field else ''}
Statut actuel : {status or 'non précisé'}
Pays : {country}
Compétences déclarées : {skills}
Objectifs actifs : {goals}"""

    if intent_summaries:
        summaries_lines = "\n".join(
            f"  {idx + 1}. [{intent_types[idx] if idx < len(intent_types) else '—'}] {s}"
            for idx, s in enumerate(intent_summaries)
        )
        profile_block += f"\n\nIntentions récentes (plus récente en premier) :\n{summaries_lines}"
        if intent_types:
            types_str = " / ".join(intent_types)
            profile_block += f"\nTypes d'intentions observés : {types_str}"
        if len(intent_domains) > 1:
            profile_block += f"\nDomaines récurrents : {', '.join(intent_domains)}"
        if keywords_str:
            profile_block += f"\nMots-clés récurrents : {keywords_str}"

    # ── Données marché ────────────────────────────────────────────────────────
    tendances_lines = "\n".join(
        f"  - {t['label']} ({t['type']}): {t['count']} offres, {t['variation_pct']:+d}% vs sem. préc."
        for t in week.get("tendances", [])
    ) or "  Aucune tendance"

    pays_lines = "\n".join(
        f"  {p['pays']}: {p['count']}"
        for p in week.get("top_pays", [])[:3]
    ) or "  Non disponible"

    comp_lines = "\n".join(
        f"  - {c['competence']}: {c['pct_offres']}% des offres, variation {c['variation_pts']:+d} pts"
        for c in competences
    ) or "  Aucune compétence détectée"

    type_lines = "\n".join(
        f"  {k}: {v}"
        for k, v in sorted(globale.get("par_type", {}).items(), key=lambda x: -x[1])[:5]
    ) or "  Non disponible"

    signal_str = (
        f"{signal['label']}: +{signal['croissance_pct']}% ({signal['count']} occurrences)"
        if signal else "Aucun signal fort"
    )

    # ── Templates JSON ────────────────────────────────────────────────────────
    tendances_tpl = ",\n".join(
        f'    {{"type": "{t["type"]}", "ce_qui_se_passe": "...", "signification": "...", "action": "..."}}'
        for t in week.get("tendances", [])
    )
    competences_tpl = ",\n".join(
        f'    {{"competence": "{c["competence"]}", "pourquoi": "...", "si_tu_as": "...", "si_tu_nas_pas": "..."}}'
        for c in competences
    )
    signal_field = '"..."' if signal else "null"

    return f"""{profile_block}

=== DONNÉES MARCHÉ ===

CETTE SEMAINE EN AFRIQUE ({week.get('total_offres', 0)} offres indexées) :
Tendances dominantes :
{tendances_lines}
Marchés actifs :
{pays_lines}

TON PAYS : {mon_pays.get('pays', 'N/A')} — 30 derniers jours
Emplois: {mon_pays.get('emplois', 0)} | Financements: {mon_pays.get('financements', 0)} | Missions: {mon_pays.get('missions', 0)} | Appels candidature: {mon_pays.get('appels_offre', 0)} | Bourses: {mon_pays.get('bourses', 0)}

COMPÉTENCES MONTANTES (dans les offres du marché) :
{comp_lines}

VUE GLOBALE ({globale.get('total_offres', 0)} offres) :
Répartition :
{type_lines}
Signal semaine : {signal_str}

=== INSTRUCTION ===
Génère les interprétations POUR CET UTILISATEUR SPÉCIFIQUEMENT.
- Si ses objectifs actifs sont "{goals}" — oriente chaque texte vers ces objectifs
- Si son domaine est "{domain}" — cite des exemples concrets de ce domaine
- Ses intentions récentes couvrent : "{" / ".join(intent_types) or "non renseigné"}" — tiens compte de cette diversité si elle existe
- Dis-lui CE QUE ÇA VEUT DIRE POUR LUI, pas pour "les utilisateurs en général"
- Les actions doivent être réalisables immédiatement par quelqu'un avec son profil

Retourne UNIQUEMENT ce JSON (remplis les "...") :
{{
  "tendances": [
{tendances_tpl}
  ],
  "mon_pays": {{
    "emplois": "...",
    "financements": "...",
    "missions": "...",
    "appels_offre": "...",
    "bourses": "..."
  }},
  "competences": [
{competences_tpl}
  ],
  "vue_globale": {{
    "geographie": "...",
    "repartition": "...",
    "signal": {signal_field},
    "action_globale": "..."
  }}
}}"""
