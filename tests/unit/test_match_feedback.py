"""Tests de la boucle de feedback et du multi-objectifs (phase 3)."""

import pytest

from app.models.user_offer_feedback import FeedbackAction
from app.repositories.feedback_repo import _AFFINITY_WEIGHT
from app.services.match_runner import (
    AFFINITY_MAX_ADJUSTMENT,
    MAX_GOALS_PER_RUN,
    _apply_type_affinity,
    _match_score_of,
)


def _offer(otype, score=60.0):
    return {
        "offer_ref": f"scraped:{otype}",
        "type": otype,
        "match_score": score,
        "match_mode": "lexical",
    }


class TestTypeAffinity:
    def test_un_type_ecarte_est_penalise(self):
        """Le coeur de P5 : écarter dix offres d'un type doit compter."""
        offers = [_offer("job")]
        _apply_type_affinity(offers, {"job": -1.0})
        assert _match_score_of(offers[0]) == 60.0 - AFFINITY_MAX_ADJUSTMENT

    def test_un_type_apprecie_est_renforce(self):
        offers = [_offer("scholarship")]
        _apply_type_affinity(offers, {"scholarship": 1.0})
        assert _match_score_of(offers[0]) == 60.0 + AFFINITY_MAX_ADJUSTMENT

    def test_ajustement_proportionnel_a_l_affinite(self):
        offers = [_offer("job")]
        _apply_type_affinity(offers, {"job": 0.5})
        assert _match_score_of(offers[0]) == pytest.approx(
            60.0 + AFFINITY_MAX_ADJUSTMENT * 0.5
        )

    def test_type_jamais_evalue_reste_intact(self):
        offers = [_offer("grant")]
        _apply_type_affinity(offers, {"job": -1.0})
        assert _match_score_of(offers[0]) == 60.0

    def test_aucune_affinite_ne_change_rien(self):
        offers = [_offer("job", 71.5)]
        _apply_type_affinity(offers, {})
        assert _match_score_of(offers[0]) == 71.5

    def test_le_score_reste_borne(self):
        haut = [_offer("job", 98.0)]
        _apply_type_affinity(haut, {"job": 1.0})
        assert _match_score_of(haut[0]) == 100.0

        bas = [_offer("job", 3.0)]
        _apply_type_affinity(bas, {"job": -1.0})
        assert _match_score_of(bas[0]) == 0.0

    def test_l_ajustement_ne_domine_pas_la_pertinence(self):
        """Le feedback affine un classement, il ne le renverse pas."""
        pertinente_mal_aimee = [_offer("job", 90.0)]
        mediocre_appreciee = [_offer("scholarship", 60.0)]
        _apply_type_affinity(pertinente_mal_aimee, {"job": -1.0})
        _apply_type_affinity(mediocre_appreciee, {"scholarship": 1.0})
        assert _match_score_of(pertinente_mal_aimee[0]) > _match_score_of(
            mediocre_appreciee[0]
        )

    def test_la_trace_de_l_ajustement_est_conservee(self):
        offers = [_offer("job")]
        _apply_type_affinity(offers, {"job": -0.75})
        assert offers[0]["affinity_applied"] == -0.75


class TestAffinityWeights:
    def test_postuler_est_le_signal_positif_le_plus_fort(self):
        assert _AFFINITY_WEIGHT[FeedbackAction.applied] == max(
            _AFFINITY_WEIGHT.values()
        )

    def test_ignorer_est_le_signal_negatif_le_plus_fort(self):
        assert _AFFINITY_WEIGHT[FeedbackAction.ignored] == min(
            _AFFINITY_WEIGHT.values()
        )

    def test_cliquer_pese_moins_que_sauvegarder(self):
        """Un clic est de la curiosité, une sauvegarde est une intention."""
        assert (
            _AFFINITY_WEIGHT[FeedbackAction.clicked]
            < _AFFINITY_WEIGHT[FeedbackAction.saved]
        )

    def test_tous_les_poids_sont_bornes(self):
        assert all(-1.0 <= w <= 1.0 for w in _AFFINITY_WEIGHT.values())


class TestMultiGoalQuota:
    @pytest.mark.parametrize(
        "top_k,goals,attendu",
        [(6, 1, 6), (6, 2, 3), (6, 3, 2), (5, 3, 1), (2, 3, 1)],
    )
    def test_le_quota_repartit_sans_augmenter_le_volume(self, top_k, goals, attendu):
        """Couvrir plus d'objectifs ne doit pas produire plus de sollicitations."""
        quota = max(1, top_k // goals)
        assert quota == attendu
        assert quota * goals <= max(top_k, goals)

    def test_le_nombre_d_objectifs_est_borne(self):
        """Au-delà, chaque objectif recevrait une offre unique."""
        assert 1 < MAX_GOALS_PER_RUN <= 5
