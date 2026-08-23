"""Détection de troncature et relance LLM.

Le format de réponse standard des agents (cf. app/agents/base.py) se termine par
le marqueur `@@END@@` dès qu'une réponse porte des clarifications, des étapes,
des suggestions, des offres ou des métiers — c'est-à-dire le cas nominal.

Ce marqueur finit par « @ », qui n'appartient pas aux caractères terminaux :
chaque réponse de ce type était donc jugée tronquée et déclenchait deux relances
LLM supplémentaires, dont le texte était ensuite jeté par _parse_meta_block.
Coût et latence triplés pour un résultat identique. Ces tests verrouillent la
correction sans relâcher la détection des vraies troncatures.
"""

from __future__ import annotations

import asyncio

from app.llm.continuation import _looks_truncated, complete_with_continuation

_LONG = "x" * 250


class _CountingProvider:
    """Provider factice : compte les appels et renvoie des réponses scriptées."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls = 0

    async def complete(self, messages: list[dict], **kwargs) -> str:
        self.calls += 1
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestDetectionTroncature:
    def test_bloc_meta_ferme_est_complet(self):
        raw = _LONG + "\n\n@@META@@\nsuggestions: Voir les offres\n@@END@@"
        assert _looks_truncated(raw) is False

    def test_bloc_meta_ferme_avec_saut_de_ligne_final(self):
        """rstrip() retire le saut de ligne, le dernier caractère redevient « @ »."""
        raw = _LONG + "\n\n@@META@@\nsteps: Étape 1 :: description\n@@END@@\n"
        assert _looks_truncated(raw) is False

    def test_phrase_coupee_reste_detectee(self):
        """Le correctif ne doit pas relâcher la détection des vraies troncatures."""
        assert _looks_truncated(_LONG + " et ensuite il faudra que tu") is True

    def test_texte_termine_normalement(self):
        assert _looks_truncated(_LONG + " Voici la marche à suivre.") is False

    def test_texte_court_jamais_tronque(self):
        assert _looks_truncated("Bonjour, comment puis-je t'aider ?") is False

    def test_texte_vide(self):
        assert _looks_truncated("") is False


class TestNombreDAppelsLLM:
    def test_reponse_avec_bloc_meta_ne_declenche_quun_appel(self):
        provider = _CountingProvider([_LONG + "\n\n@@META@@\nsteps: A :: b\n@@END@@"])
        _run(complete_with_continuation(provider, [{"role": "user", "content": "x"}]))
        assert provider.calls == 1, "le cas nominal doit coûter un seul appel LLM"

    def test_vraie_troncature_declenche_bien_une_relance(self):
        """Contre-épreuve : la relance doit continuer de fonctionner."""
        provider = _CountingProvider([
            _LONG + " et ensuite il faudra que tu",   # tronquée → relance
            "termines ton dossier.",                   # complète → arrêt
        ])
        result = _run(complete_with_continuation(provider, [{"role": "user", "content": "x"}]))
        assert provider.calls == 2
        assert result.endswith("termines ton dossier.")

    def test_relances_plafonnees(self):
        """Une réponse toujours tronquée ne doit jamais boucler indéfiniment."""
        provider = _CountingProvider([_LONG + " toujours coupé net et"])
        _run(complete_with_continuation(provider, [{"role": "user", "content": "x"}], max_rounds=3))
        assert provider.calls == 3
