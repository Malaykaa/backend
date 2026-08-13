"""Tests des primitives d'agrégation du dashboard analytique.

Ces trois fonctions produisent les chiffres de la vingtaine de graphiques du
tableau de bord : une erreur ici se propage partout sans être visible à l'œil.
Elles sont testées isolément, sans base de données.
"""

from datetime import datetime, timezone

import pytest

from app.services.admin_analytics_service import (
    _UNKNOWN_KEY,
    _bucketize,
    _geo_country,
    _month_key,
    _month_label,
    _window_start,
)


class TestBucketize:
    def test_pourcentages_sur_le_total_reel(self):
        buckets = _bucketize([("a", 3), ("b", 1)])
        assert [b.count for b in buckets] == [3, 1]
        assert [b.pct for b in buckets] == [75.0, 25.0]

    def test_tri_par_effectif_decroissant(self):
        buckets = _bucketize([("petit", 1), ("gros", 50), ("moyen", 10)])
        assert [b.key for b in buckets] == ["gros", "moyen", "petit"]

    def test_les_valeurs_nulles_deviennent_non_renseigne(self):
        """Un taux de remplissage faible est une information, pas un déchet."""
        buckets = _bucketize([("CI", 5), (None, 3), ("", 2)])
        unknown = [b for b in buckets if b.key == _UNKNOWN_KEY]
        assert len(unknown) == 1
        assert unknown[0].count == 5  # None + "" fusionnés

    def test_non_renseigne_termine_toujours_la_liste(self):
        """Même majoritaire, il ne doit pas occuper la tête d'un graphique."""
        buckets = _bucketize([(None, 100), ("CI", 1)])
        assert buckets[-1].key == _UNKNOWN_KEY
        assert buckets[0].key == "CI"

    def test_la_queue_est_regroupee_en_autres(self):
        rows = [(f"k{i}", 10 - i) for i in range(12)]
        buckets = _bucketize(rows, top_n=3)
        assert len(buckets) == 4
        assert buckets[-1].key == "_others"

    def test_les_pourcentages_somment_a_cent(self):
        """Vrai même après regroupement — le total est calculé avant troncature."""
        rows = [(f"k{i}", i + 1) for i in range(20)]
        buckets = _bucketize(rows, top_n=5)
        assert sum(b.pct for b in buckets) == pytest.approx(100.0, abs=0.2)

    def test_les_comptes_sont_conserves_apres_regroupement(self):
        rows = [(f"k{i}", 2) for i in range(15)]
        buckets = _bucketize(rows, top_n=4)
        assert sum(b.count for b in buckets) == 30

    def test_libelles_appliques(self):
        buckets = _bucketize([("M", 2)], labels={"M": "Homme"})
        assert buckets[0].label == "Homme"

    def test_cle_sans_libelle_reste_brute(self):
        """Les codes pays sont résolus côté frontend, pas ici."""
        assert _bucketize([("CI", 1)])[0].label == "CI"

    def test_enum_accepte(self):
        """Les colonnes SQLAlchemy Enum renvoient des objets, pas des chaînes."""
        class FakeEnum:
            value = "job"
        assert _bucketize([(FakeEnum(), 4)])[0].key == "job"

    def test_liste_vide(self):
        assert _bucketize([]) == []

    def test_total_nul(self):
        assert _bucketize([("a", 0)]) == []


class TestGeoNormalisation:
    @pytest.mark.parametrize("raw,attendu", [
        ("Abidjan, Côte d'Ivoire", "Côte d'Ivoire"),
        ("COTE D IVOIRE", "Côte d'Ivoire"),
        ("Dakar, Senegal", "Sénégal"),
    ])
    def test_texte_libre_ramene_au_pays(self, raw, attendu):
        assert _geo_country(raw) == attendu

    def test_nigeria_ne_matche_pas_niger(self):
        """_AFRICAN_GEO est trié du plus long au plus court pour cette raison."""
        assert _geo_country("Lagos, Nigeria") == "Nigeria"

    def test_valeur_courte_inconnue_conservee(self):
        assert _geo_country("Quebec") == "Quebec"

    def test_adresse_longue_ecartee(self):
        """Une adresse complète n'est pas une dimension d'analyse exploitable."""
        long_addr = "12 rue de la Paix, quartier administratif, immeuble B, 3e etage"
        assert _geo_country(long_addr) == _UNKNOWN_KEY

    def test_code_iso_resolu(self):
        assert _geo_country("CI") == "Côte d'Ivoire"
        assert _geo_country("ci") == "Côte d'Ivoire"

    def test_code_et_nom_convergent(self):
        """Régression observée en base : 6 profils en « CI », 7 en « Côte d'Ivoire »."""
        assert _geo_country("CI") == _geo_country("Côte d'Ivoire")

    def test_les_trois_formes_fusionnent_en_une_part(self):
        """Le défaut réel : un même pays occupait deux barres du graphique."""
        buckets = _bucketize(
            [("CI", 6), ("Côte d'Ivoire", 7), ("Abidjan, Côte d'Ivoire", 2)],
            normalize_geo=True,
        )
        assert len(buckets) == 1
        assert buckets[0].key == "Côte d'Ivoire"
        assert buckets[0].count == 15
        assert buckets[0].pct == 100.0


class TestFenetreTemporelle:
    def test_debut_tronque_au_mois(self):
        """Sinon le premier point serait un mois partiel et simulerait une chute."""
        now = datetime(2026, 8, 13, 17, 45, tzinfo=timezone.utc)
        assert _window_start(now, 12) == datetime(2025, 9, 1, tzinfo=timezone.utc)

    def test_fenetre_inclut_le_mois_courant(self):
        now = datetime(2026, 8, 13, tzinfo=timezone.utc)
        start = _window_start(now, 3)
        assert start == datetime(2026, 6, 1, tzinfo=timezone.utc)

    def test_passage_d_annee(self):
        now = datetime(2026, 2, 10, tzinfo=timezone.utc)
        assert _window_start(now, 6) == datetime(2025, 9, 1, tzinfo=timezone.utc)

    def test_fenetre_d_un_mois(self):
        now = datetime(2026, 8, 13, tzinfo=timezone.utc)
        assert _window_start(now, 1) == datetime(2026, 8, 1, tzinfo=timezone.utc)

    def test_cle_de_mois_triable_lexicalement(self):
        assert _month_key(datetime(2026, 1, 5, tzinfo=timezone.utc)) == "2026-01"
        assert "2026-01" < "2026-10"

    def test_libelle_de_mois_francais(self):
        assert _month_label(datetime(2026, 1, 5, tzinfo=timezone.utc)) == "janv. 26"
        assert _month_label(datetime(2026, 12, 5, tzinfo=timezone.utc)) == "déc. 26"
