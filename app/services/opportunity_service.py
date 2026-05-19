"""OpportunityService — matching profil + intention extraite → opportunités.

Scoring en deux couches :
1. Score profil (domaine + pays + statut) — max 100 pts
2. Bonus intention extraite (type + domain + keywords + location) — max 60 pts
   Score final normalisé sur 100.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.opportunity import Opportunity, UserOpportunity
from app.models.user import Profile
from app.models.user_intent import UserIntent
from app.repositories.intent_repo import IntentRepository
from app.repositories.opportunity_repo import OpportunityRepository


class OpportunityService:
    def __init__(self, db: Session) -> None:
        self.repo = OpportunityRepository(db)
        self._intent_repo = IntentRepository(db)

    def match_for_user(
        self,
        user_id: uuid.UUID,
        profile: Profile | None,
    ) -> list[dict]:
        """Calcule le score de chaque opportunité active pour l'utilisateur.

        Utilise le profil statique ET les intentions extraites des conversations
        pour un matching plus précis. Retourne une liste triée par score décroissant.
        """
        opportunities = self.repo.get_active()

        # Charger les dernières intentions extraites pour cet utilisateur
        intents = self._intent_repo.get_latest_for_user(user_id, limit=20)

        results = []
        for opp in opportunities:
            base_score = self._score_from_profile(opp, profile)

            # Bonus : meilleure intention parmi toutes (max match)
            intent_bonus = max(
                (self._score_from_intent(opp, intent) for intent in intents),
                default=0.0,
            )

            # Normaliser sur 100 (max théorique = 160)
            final_score = min(base_score + intent_bonus, 160.0) / 1.6

            match = self.repo.upsert_match(
                user_id=user_id,
                opportunity_id=opp.id,
                score=final_score,
            )
            results.append({
                "opportunity": opp,
                "score": final_score,
                "status": match.status,
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    # ── Couche 1 : profil statique (max 100 pts) ─────────────────────────────

    @staticmethod
    def _score_from_profile(opp: Opportunity, profile: Profile | None) -> float:
        if not profile:
            return 10.0

        score = 10.0

        # Domaine (40 pts max)
        if profile.domain and opp.domain:
            p = profile.domain.lower()
            o = opp.domain.lower()
            if p == o:
                score += 40.0
            elif p in o or o in p:
                score += 25.0

        # Pays (30 pts max)
        if profile.country and opp.country:
            p = profile.country.lower()
            o = opp.country.lower()
            if p == o:
                score += 30.0
            elif o in ("international", "global", "monde"):
                score += 15.0

        # Statut (20 pts max)
        if profile.current_status and opp.type:
            status_lower = profile.current_status.lower()
            type_val = opp.type.value
            status_type_map = {
                "étudiant":        ["scholarship", "internship", "grant"],
                "étudiante":       ["scholarship", "internship", "grant"],
                "diplômé":         ["job", "internship", "grant"],
                "diplômée":        ["job", "internship", "grant"],
                "entrepreneur":    ["funding", "tender", "grant"],
                "développeur":     ["job", "internship"],
                "développeuse":    ["job", "internship"],
                "chercheur":       ["grant", "scholarship"],
                "chercheuse":      ["grant", "scholarship"],
                "professionnel":   ["job", "funding", "tender"],
                "professionnelle": ["job", "funding", "tender"],
            }
            for status_key, matching_types in status_type_map.items():
                if status_key in status_lower and type_val in matching_types:
                    score += 20.0
                    break

        return min(score, 100.0)

    # ── Couche 2 : intention extraite (bonus max 60 pts) ─────────────────────

    @staticmethod
    def _score_from_intent(opp: Opportunity, intent: UserIntent) -> float:
        """Bonus calculé depuis l'intention extraite par le LLM.

        - Type d'intention ↔ type d'opportunité : 25 pts
        - Domaine intent ↔ domaine opportunité   : 20 pts
        - Keywords intent dans titre+description  : 10 pts
        - Location intent ↔ pays opportunité      :  5 pts
        """
        bonus = 0.0

        # Type (25 pts)
        intent_to_opp: dict[str, list[str]] = {
            "stage":           ["internship"],
            "emploi":          ["job"],
            "bourse":          ["scholarship", "grant"],
            "financement":     ["funding", "grant"],
            "appel_offre":     ["tender"],
            "formation":       ["scholarship", "grant"],
            "reconversion":    ["job", "internship"],
            "entrepreneuriat": ["funding", "grant", "tender"],
            "partenariat":     ["partnership"],
        }
        if intent.intent_type and opp.type:
            if opp.type.value in intent_to_opp.get(intent.intent_type, []):
                bonus += 25.0

        # Domaine (20 pts)
        if intent.domain and opp.domain:
            i_d = intent.domain.lower()
            o_d = opp.domain.lower()
            if i_d == o_d:
                bonus += 20.0
            elif i_d in o_d or o_d in i_d:
                bonus += 12.0

        # Keywords (10 pts)
        if intent.keywords:
            opp_text = f"{opp.title or ''} {opp.description or ''}".lower()
            matches = sum(
                1 for kw in intent.keywords if kw and kw.lower() in opp_text
            )
            if matches >= 2:
                bonus += 10.0
            elif matches == 1:
                bonus += 5.0

        # Location (5 pts)
        if intent.location and opp.country:
            i_loc = intent.location.lower()
            o_loc = opp.country.lower()
            if i_loc in o_loc or o_loc in i_loc:
                bonus += 5.0

        return bonus
