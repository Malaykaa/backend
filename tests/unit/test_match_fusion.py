"""Tests de la recherche hybride (phase 2).

Garantie centrale : sans clé d'embeddings, la fusion doit reproduire
exactement le classement lexical de la phase 1 — aucune régression pour
la production actuelle.
"""

import pytest

from app.services.match_runner import NOTIFY_THRESHOLD, _match_score_of
from app.services.scraped_offer_service import (
    MATCH_MODE_HYBRID,
    MATCH_MODE_LEXICAL,
    MATCH_MODE_SEMANTIC,
    _CORROBORATION_BONUS,
    _fuse_results,
)


def _res(ref, match_score=50.0, mode=MATCH_MODE_LEXICAL, relevance=1.0):
    return {
        "id": ref,
        "offer_ref": f"scraped:{ref}",
        "title": f"Offre {ref}",
        "match_score": match_score,
        "match_mode": mode,
        "relevance_score": relevance,
    }


def _refs(results):
    return [r["offer_ref"] for r in results]


# ── Non-régression : le mode sans clé ─────────────────────────────────────


class TestDegradedModeUnchanged:
    def test_liste_semantique_vide_preserve_l_ordre_lexical(self):
        """Sans PERPLEXITY_API_KEY, la fusion ne doit rien réordonner."""
        lexical = [_res("a", 90), _res("b", 70), _res("c", 50), _res("d", 30)]
        fused = _fuse_results([], lexical, limit=10)
        assert _refs(fused) == _refs(lexical)

    def test_les_scores_lexicaux_sont_intacts(self):
        lexical = [_res("a", 88.5), _res("b", 61.25)]
        fused = _fuse_results([], lexical, limit=10)
        assert [f["match_score"] for f in fused] == [88.5, 61.25]
        assert all(f["match_mode"] == MATCH_MODE_LEXICAL for f in fused)

    def test_les_deux_listes_vides(self):
        assert _fuse_results([], [], limit=10) == []

    def test_liste_lexicale_vide_preserve_l_ordre_semantique(self):
        semantic = [_res("x", 95, MATCH_MODE_SEMANTIC), _res("y", 80, MATCH_MODE_SEMANTIC)]
        fused = _fuse_results(semantic, [], limit=10)
        assert _refs(fused) == _refs(semantic)
        assert all(f["match_mode"] == MATCH_MODE_SEMANTIC for f in fused)


# ── Le coeur : la fusion ──────────────────────────────────────────────────


class TestFusion:
    def test_l_accord_des_deux_voies_fait_remonter(self):
        """À pertinence égale, l'offre corroborée passe devant."""
        semantic = [_res("commune", 70, MATCH_MODE_SEMANTIC)]
        lexical = [_res("solo", 70), _res("commune", 70)]
        fused = _fuse_results(semantic, lexical, limit=10)
        assert fused[0]["offer_ref"] == "scraped:commune"

    def test_offre_vue_deux_fois_est_marquee_hybride(self):
        fused = _fuse_results(
            [_res("z", 70, MATCH_MODE_SEMANTIC)], [_res("z", 40)], limit=10
        )
        assert len(fused) == 1
        assert fused[0]["match_mode"] == MATCH_MODE_HYBRID

    def test_la_confiance_retenue_est_le_maximum(self):
        """Trouvée par une seule voie n'est pas une preuve contraire."""
        fused = _fuse_results(
            [_res("z", 85, MATCH_MODE_SEMANTIC)], [_res("z", 40)], limit=10
        )
        assert fused[0]["match_score"] == 85.0

    def test_le_maximum_joue_dans_les_deux_sens(self):
        fused = _fuse_results(
            [_res("z", 30, MATCH_MODE_SEMANTIC)], [_res("z", 92)], limit=10
        )
        assert fused[0]["match_score"] == 92.0

    def test_deduplication_par_offer_ref(self):
        fused = _fuse_results(
            [_res("a"), _res("b")], [_res("b"), _res("a")], limit=10
        )
        assert len(fused) == 2

    def test_une_offre_absente_de_l_index_reste_atteignable(self):
        """Le coeur de P2 : plus de corpus masqué par la bascule."""
        semantic = [_res("indexee", 55, MATCH_MODE_SEMANTIC)]
        lexical = [_res("non_indexee", 95), _res("indexee", 55)]
        fused = _fuse_results(semantic, lexical, limit=10)
        assert "scraped:non_indexee" in _refs(fused)

    def test_respect_de_la_limite(self):
        many = [_res(str(i)) for i in range(30)]
        assert len(_fuse_results([], many, limit=5)) == 5

    def test_entrees_sans_identifiant_sont_ignorees(self):
        fused = _fuse_results([], [{"title": "orpheline"}, _res("ok")], limit=10)
        assert _refs(fused) == ["scraped:ok"]


# ── Propriétés de la fusion ───────────────────────────────────────────────


class TestFusionProperties:
    def test_la_corroboration_ne_renverse_pas_un_ecart_reel(self):
        """Le défaut qui a fait écarter RRF : 52 % corroboré passait devant 94 % solo."""
        semantic = [_res("mediocre", 52, MATCH_MODE_SEMANTIC)]
        lexical = [_res("excellente", 94), _res("mediocre", 48)]
        fused = _fuse_results(semantic, lexical, limit=10)
        assert fused[0]["offer_ref"] == "scraped:excellente"

    def test_la_corroboration_departage_a_pertinence_proche(self):
        semantic = [_res("corroboree", 80, MATCH_MODE_SEMANTIC)]
        lexical = [_res("solo", 82), _res("corroboree", 78)]
        fused = _fuse_results(semantic, lexical, limit=10)
        assert fused[0]["offer_ref"] == "scraped:corroboree"

    def test_le_bonus_suit_l_avis_de_la_voie_la_plus_faible(self):
        """Un accord franc vaut plus qu'un accord tiède."""
        franc = _fuse_results(
            [_res("a", 80, MATCH_MODE_SEMANTIC)], [_res("a", 76)], limit=1
        )
        tiede = _fuse_results(
            [_res("b", 80, MATCH_MODE_SEMANTIC)], [_res("b", 8)], limit=1
        )
        # Même confiance affichée, mais l'ordre interne diffère : on le vérifie
        # en les mettant en concurrence avec une offre solo intermédiaire.
        mix = _fuse_results(
            [_res("fort", 80, MATCH_MODE_SEMANTIC), _res("faible", 80, MATCH_MODE_SEMANTIC)],
            [_res("fort", 76), _res("faible", 8), _res("solo", 84)],
            limit=10,
        )
        assert franc[0]["match_score"] == tiede[0]["match_score"] == 80.0
        assert _refs(mix)[0] == "scraped:fort"
        assert _refs(mix).index("scraped:solo") < _refs(mix).index("scraped:faible")

    def test_le_bonus_est_borne(self):
        """Il ne peut jamais ajouter plus que sa constante."""
        parfait = _fuse_results(
            [_res("a", 90, MATCH_MODE_SEMANTIC)], [_res("a", 100)], limit=1
        )
        assert parfait[0]["match_score"] == 100.0
        assert _CORROBORATION_BONUS <= 15.0

    def test_le_champ_interne_peer_ne_fuit_pas(self):
        fused = _fuse_results([_res("a", 60, MATCH_MODE_SEMANTIC)], [_res("a")], limit=5)
        assert "_peer" not in fused[0]


# ── Seuils de notification ────────────────────────────────────────────────


class TestHybridThreshold:
    def test_le_mode_hybride_a_son_seuil(self):
        assert MATCH_MODE_HYBRID in NOTIFY_THRESHOLD

    def test_hybride_entre_lexical_et_semantique(self):
        """Corroboré par deux voies : plus sûr que lexical seul, pas autant que le vecteur."""
        assert (
            NOTIFY_THRESHOLD[MATCH_MODE_LEXICAL]
            < NOTIFY_THRESHOLD[MATCH_MODE_HYBRID]
            < NOTIFY_THRESHOLD[MATCH_MODE_SEMANTIC]
        )

    def test_une_offre_hybride_forte_declenche_la_notification(self):
        offer = {"match_score": 78.0, "match_mode": MATCH_MODE_HYBRID}
        assert _match_score_of(offer) >= NOTIFY_THRESHOLD[MATCH_MODE_HYBRID]
