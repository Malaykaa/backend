"""Tests des métriques d'évaluation du matching (phase 3).

Ces métriques arbitreront les changements futurs : si l'une d'elles est fausse,
elle validera des régressions. Elles méritent donc d'être vérifiées contre des
cas dont on connaît la réponse à la main.
"""

import math

import pytest

from scripts.eval.metrics import (
    dcg_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarize,
)


class TestPrecision:
    def test_classement_parfait(self):
        assert precision_at_k([2, 2, 2, 2, 2], 5) == 1.0

    def test_aucune_offre_utile(self):
        assert precision_at_k([0, 0, 0, 0, 0], 5) == 0.0

    def test_moitie_utile(self):
        assert precision_at_k([2, 0, 1, 0, 2], 5) == pytest.approx(0.6)

    def test_acceptable_compte_comme_utile(self):
        """Une offre « acceptable » sert l'utilisateur, ce n'est pas une erreur."""
        assert precision_at_k([1, 1, 1, 1, 1], 5) == 1.0

    def test_liste_plus_courte_que_k(self):
        """Divise par la taille réelle, pas par k — sinon on pénalise le rappel."""
        assert precision_at_k([2, 2], 5) == 1.0

    def test_liste_vide(self):
        assert precision_at_k([], 5) == 0.0

    def test_ignore_au_dela_de_k(self):
        assert precision_at_k([2, 2, 2, 2, 2, 0, 0, 0], 5) == 1.0


class TestRecall:
    def test_toutes_les_utiles_remontees(self):
        assert recall_at_k([2, 1, 0], 10, total_relevant=2) == 1.0

    def test_moitie_remontee(self):
        assert recall_at_k([2, 0, 0], 10, total_relevant=2) == 0.5

    def test_aucune_utile_dans_le_corpus(self):
        assert recall_at_k([0, 0], 10, total_relevant=0) == 0.0

    def test_borne_a_un(self):
        assert recall_at_k([2, 2, 2], 10, total_relevant=2) == 1.0

    def test_respecte_la_coupure_k(self):
        assert recall_at_k([0, 0, 0, 2], 3, total_relevant=1) == 0.0


class TestReciprocalRank:
    def test_premiere_position(self):
        assert reciprocal_rank([2, 0, 0]) == 1.0

    def test_troisieme_position(self):
        assert reciprocal_rank([0, 0, 1]) == pytest.approx(1 / 3)

    def test_aucune_utile(self):
        assert reciprocal_rank([0, 0, 0]) == 0.0

    def test_liste_vide(self):
        assert reciprocal_rank([]) == 0.0


class TestNDCG:
    def test_ordre_ideal_vaut_un(self):
        assert ndcg_at_k([2, 2, 1, 0], 10) == 1.0

    def test_ordre_inverse_penalise(self):
        assert ndcg_at_k([0, 1, 2, 2], 10) < ndcg_at_k([2, 2, 1, 0], 10)

    def test_aucune_utile(self):
        assert ndcg_at_k([0, 0, 0], 10) == 0.0

    def test_distingue_pertinente_et_acceptable(self):
        """Ce que precision@k confond."""
        assert ndcg_at_k([2, 1], 10) > ndcg_at_k([1, 2], 10)

    def test_dcg_suit_la_formule(self):
        # rang 1 : (2^2-1)/log2(2) = 3 ; rang 2 : (2^1-1)/log2(3)
        attendu = 3.0 / math.log2(2) + 1.0 / math.log2(3)
        assert dcg_at_k([2, 1], 10) == pytest.approx(attendu)


class TestSummarize:
    def test_moyenne_par_cas(self):
        cases = [
            {"precision@5": 1.0, "recall@10": 1.0, "mrr": 1.0, "ndcg@10": 1.0},
            {"precision@5": 0.0, "recall@10": 0.0, "mrr": 0.0, "ndcg@10": 0.0},
        ]
        out = summarize(cases)
        assert out["precision@5"] == 0.5
        assert set(out) == {"precision@5", "recall@10", "mrr", "ndcg@10"}

    def test_aucun_cas(self):
        assert summarize([]) == {}

    def test_chaque_intention_pese_pareil(self):
        """Moyenne par cas, pas par offre : sinon les cas riches écrasent les autres."""
        cases = [
            {"precision@5": 1.0, "recall@10": 0.0, "mrr": 0.0, "ndcg@10": 0.0},
            {"precision@5": 0.0, "recall@10": 0.0, "mrr": 0.0, "ndcg@10": 0.0},
            {"precision@5": 0.0, "recall@10": 0.0, "mrr": 0.0, "ndcg@10": 0.0},
        ]
        assert summarize(cases)["precision@5"] == pytest.approx(1 / 3)
