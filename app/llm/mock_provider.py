"""Mock LLM provider — réponses déterministes pour dev et tests (coût = 0)."""

import json
from typing import AsyncIterator


class MockProvider:
    """Retourne des réponses structurées fixes, pas d'appel réseau."""

    async def complete(self, messages: list[dict], **kwargs) -> str:
        # Détecter le type de requête via le system prompt
        system_prompt = ""
        for m in messages:
            if m.get("role") == "system":
                system_prompt = m.get("content", "")
                break

        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

        # Triage unifié (routeur) : intent + confidence + mode
        if "classificateur" in system_prompt.lower() or "routeur" in system_prompt.lower():
            return json.dumps(self._triage_response(last_user_msg), ensure_ascii=False)

        # Réponse agent standard
        return json.dumps(self._build_response(last_user_msg), ensure_ascii=False)

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        """Yield la réponse mot par mot pour simuler le streaming."""
        result = await self.complete(messages, **kwargs)
        words = result.split(" ")
        for i, word in enumerate(words):
            yield word if i == 0 else f" {word}"

    # ── Classification mock ──────────────────────────────

    def _triage_response(self, user_message: str) -> dict:
        """Retourne un JSON de triage unifié : intent + confidence + mode."""
        lower = user_message.lower()

        # Détecter les signaux de workflow complexe
        workflow_signals = ["de a à z", "de a a z", "complet", "tout ce qu'il faut", "prépare tout", "fais tout"]
        if any(signal in lower for signal in workflow_signals):
            return {
                "intent": "scholarship",
                "confidence": 0.9,
                "mode": "workflow",
                "steps": [
                    {"agent_type": "scholarship", "task": "Analyser les critères", "order": 1},
                    {"agent_type": "document", "task": "Préparer le CV", "order": 2},
                ],
            }

        # Mode direct : classifier l'intent
        if any(w in lower for w in ["examen", "exam", "concours", "bac", "brevet", "test", "épreuve", "partiel"]):
            return {"intent": "exam", "confidence": 0.95, "mode": "direct"}
        if any(w in lower for w in ["bourse", "scholarship", "stage"]):
            return {"intent": "scholarship", "confidence": 0.92, "mode": "direct"}
        if any(w in lower for w in ["financement", "funding", "prêt", "investiss", "subvention"]):
            return {"intent": "funding", "confidence": 0.90, "mode": "direct"}
        if any(w in lower for w in ["appel d'offre", "tender", "marché", "soumission"]):
            return {"intent": "tender", "confidence": 0.90, "mode": "direct"}
        if any(w in lower for w in ["étude à l'étranger", "study grant", "université", "master"]):
            return {"intent": "study_grant", "confidence": 0.88, "mode": "direct"}
        if any(w in lower for w in ["reconversion", "career", "carrière", "métier", "emploi"]):
            return {"intent": "career", "confidence": 0.90, "mode": "direct"}
        if any(w in lower for w in ["cv", "lettre de motivation", "business plan", "email pro"]):
            return {"intent": "document", "confidence": 0.95, "mode": "direct"}

        # Salutations simples → free avec confiance basse (pas de vraie intention)
        greetings = ["bonjour", "salut", "hello", "hi", "hey", "coucou", "bonsoir"]
        if any(lower.strip().startswith(g) for g in greetings):
            return {"intent": "free", "confidence": 0.4, "mode": "direct"}

        # Aucun mot-clé reconnu → confiance basse
        return {"intent": "free", "confidence": 0.5, "mode": "direct"}

    # ── Réponses agent standard ──────────────────────────

    def _build_response(self, user_message: str) -> dict:
        """Construit une AgentResponse mock basée sur le message utilisateur."""
        lower = user_message.lower()

        if any(w in lower for w in ["examen", "exam", "concours", "bac", "test"]):
            return self._exam_response()
        if any(w in lower for w in ["bourse", "scholarship", "stage"]):
            return self._scholarship_response()
        if any(w in lower for w in ["financement", "funding", "prêt", "investiss"]):
            return self._funding_response()
        if any(w in lower for w in ["appel d'offre", "tender", "marché"]):
            return self._tender_response()
        if any(w in lower for w in ["étude", "study", "université", "master"]):
            return self._study_grant_response()
        if any(w in lower for w in ["reconversion", "career", "carrière", "métier"]):
            return self._career_response()
        if any(w in lower for w in ["cv", "lettre", "business plan", "email"]):
            return self._document_response()

        return self._free_response()

    # ── Réponses types par agent ──────────────────────────

    def _exam_response(self) -> dict:
        return {
            "explanation": "Je vais t'aider à préparer ton examen. Voici un plan structuré.",
            "steps": [
                {"id": "s1", "label": "Analyser le programme", "description": "Identifier les chapitres et thèmes clés du programme officiel.", "order": 1},
                {"id": "s2", "label": "Planifier les révisions", "description": "Créer un planning de révision sur les semaines restantes.", "order": 2},
                {"id": "s3", "label": "Faire des exercices", "description": "S'entraîner avec des annales et des exercices types.", "order": 3},
                {"id": "s4", "label": "Réviser les points faibles", "description": "Concentrer l'effort sur les matières ou chapitres moins maîtrisés.", "order": 4},
                {"id": "s5", "label": "Examen blanc", "description": "Faire un examen blanc en conditions réelles pour s'évaluer.", "order": 5},
            ],
            "suggestions": [
                {"id": "sg1", "label": "Si tu veux je peux te : générer une fiche de révision"},
                {"id": "sg2", "label": "Si tu veux je peux te : créer un planning personnalisé"},
                {"id": "sg3", "label": "Si tu veux je peux te : trouver des annales corrigées"},
            ],
            "sources": [
                {"title": "Méthodologie de révision", "url": "https://example.com/revision"},
            ],
            "deliverables": ["fiche_revision", "planning"],
            "clarifications": [],
            "agent_id": "exam",
        }

    def _scholarship_response(self) -> dict:
        return {
            "explanation": "Je vais t'accompagner dans ta recherche de bourse ou stage.",
            "steps": [
                {"id": "s1", "label": "Définir ton profil", "description": "Identifier tes compétences, niveau d'études et domaine.", "order": 1},
                {"id": "s2", "label": "Rechercher les bourses", "description": "Lister les bourses disponibles correspondant à ton profil.", "order": 2},
                {"id": "s3", "label": "Préparer le dossier", "description": "Rassembler les documents nécessaires (relevés, CV, lettres).", "order": 3},
                {"id": "s4", "label": "Soumettre les candidatures", "description": "Envoyer les dossiers avant les dates limites.", "order": 4},
            ],
            "suggestions": [
                {"id": "sg1", "label": "Si tu veux je peux te : rédiger une lettre de motivation"},
                {"id": "sg2", "label": "Si tu veux je peux te : lister les bourses ouvertes"},
            ],
            "sources": [],
            "deliverables": ["cover_letter"],
            "clarifications": [],
            "agent_id": "scholarship",
        }

    def _funding_response(self) -> dict:
        return {
            "explanation": "Je vais t'aider à trouver des financements pour ton projet.",
            "steps": [
                {"id": "s1", "label": "Clarifier le projet", "description": "Décrire précisément le projet et le montant recherché.", "order": 1},
                {"id": "s2", "label": "Identifier les sources", "description": "Lister les bailleurs, subventions et mécanismes disponibles.", "order": 2},
                {"id": "s3", "label": "Préparer le business plan", "description": "Rédiger un business plan convaincant.", "order": 3},
                {"id": "s4", "label": "Déposer les demandes", "description": "Soumettre les dossiers de financement.", "order": 4},
            ],
            "suggestions": [
                {"id": "sg1", "label": "Si tu veux je peux te : générer un business plan"},
                {"id": "sg2", "label": "Si tu veux je peux te : trouver des subventions"},
            ],
            "sources": [],
            "deliverables": ["business_plan"],
            "clarifications": [],
            "agent_id": "funding",
        }

    def _tender_response(self) -> dict:
        return {
            "explanation": "Je vais t'accompagner pour répondre à cet appel d'offres.",
            "steps": [
                {"id": "s1", "label": "Analyser le cahier des charges", "description": "Lire et comprendre les exigences de l'appel d'offres.", "order": 1},
                {"id": "s2", "label": "Vérifier l'éligibilité", "description": "S'assurer que ton profil/entreprise correspond aux critères.", "order": 2},
                {"id": "s3", "label": "Préparer l'offre technique", "description": "Rédiger la proposition technique détaillée.", "order": 3},
                {"id": "s4", "label": "Soumettre l'offre", "description": "Envoyer le dossier complet avant la deadline.", "order": 4},
            ],
            "suggestions": [
                {"id": "sg1", "label": "Si tu veux je peux te : analyser le cahier des charges"},
            ],
            "sources": [],
            "deliverables": ["report"],
            "clarifications": [],
            "agent_id": "tender",
        }

    def _study_grant_response(self) -> dict:
        return {
            "explanation": "Je vais t'aider à trouver une bourse d'étude à l'étranger.",
            "steps": [
                {"id": "s1", "label": "Choisir la destination", "description": "Identifier les pays et universités cibles.", "order": 1},
                {"id": "s2", "label": "Trouver les programmes", "description": "Rechercher les bourses d'études disponibles.", "order": 2},
                {"id": "s3", "label": "Préparer le dossier", "description": "CV, lettres, relevés de notes, tests de langue.", "order": 3},
                {"id": "s4", "label": "Postuler", "description": "Soumettre les candidatures dans les délais.", "order": 4},
            ],
            "suggestions": [
                {"id": "sg1", "label": "Si tu veux je peux te : rédiger ton CV académique"},
                {"id": "sg2", "label": "Si tu veux je peux te : préparer ta lettre de motivation"},
            ],
            "sources": [],
            "deliverables": ["cv", "cover_letter"],
            "clarifications": [],
            "agent_id": "study_grant",
        }

    def _career_response(self) -> dict:
        return {
            "explanation": "Je vais t'accompagner dans ta reconversion professionnelle.",
            "steps": [
                {"id": "s1", "label": "Bilan de compétences", "description": "Identifier tes compétences transférables.", "order": 1},
                {"id": "s2", "label": "Explorer les métiers", "description": "Découvrir les métiers qui correspondent à ton profil.", "order": 2},
                {"id": "s3", "label": "Plan de formation", "description": "Identifier les formations nécessaires.", "order": 3},
                {"id": "s4", "label": "Mise en action", "description": "Postuler ou commencer la formation choisie.", "order": 4},
            ],
            "suggestions": [
                {"id": "sg1", "label": "Si tu veux je peux te : mettre à jour ton CV"},
                {"id": "sg2", "label": "Si tu veux je peux te : trouver des formations"},
            ],
            "sources": [],
            "deliverables": ["cv"],
            "clarifications": [],
            "agent_id": "career",
        }

    def _document_response(self) -> dict:
        return {
            "explanation": "Je vais générer le document demandé à partir de ton profil.",
            "steps": [
                {"id": "s1", "label": "Collecte des informations", "description": "Récupérer les données de ton profil.", "order": 1},
                {"id": "s2", "label": "Génération du document", "description": "Créer le document structuré.", "order": 2},
                {"id": "s3", "label": "Révision", "description": "Vérifier et ajuster le contenu.", "order": 3},
            ],
            "suggestions": [
                {"id": "sg1", "label": "Si tu veux je peux te : modifier une section"},
            ],
            "sources": [],
            "deliverables": ["cv"],
            "clarifications": [],
            "agent_id": "document",
        }

    def _free_response(self) -> dict:
        return {
            "explanation": "Je suis Malayka, ton assistant personnel. Je peux t'aider à préparer un examen, trouver une bourse, monter un dossier de financement, répondre à un appel d'offres, ou générer des documents professionnels.",
            "steps": [],
            "suggestions": [
                {"id": "sg1", "label": "Si tu veux je peux te : aider à préparer un examen"},
                {"id": "sg2", "label": "Si tu veux je peux te : trouver une bourse d'étude"},
                {"id": "sg3", "label": "Si tu veux je peux te : chercher un financement"},
                {"id": "sg4", "label": "Si tu veux je peux te : répondre à un appel d'offres"},
                {"id": "sg5", "label": "Si tu veux je peux te : générer ton CV"},
            ],
            "sources": [],
            "deliverables": [],
            "clarifications": [],
            "agent_id": "free",
        }
