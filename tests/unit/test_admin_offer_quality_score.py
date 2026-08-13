"""Régression : les offres créées depuis le backoffice n'étaient jamais recommandées.

Cause mécanique, vérifiée dans scraped_offer_repo.search_by_keywords :

    ORDER BY quality_score DESC NULLS LAST, scraped_at DESC LIMIT :limit

`POST /admin/offers` posait `quality_score=None` et n'appelait jamais
`process_offer()` (contrairement à tous les chemins de scraping). Une offre à
NULL passe derrière toute offre ayant un score, même faible — dès que le
nombre de candidats concurrents dépasse la limite de la requête, elle
n'entre jamais dans le lot que Python re-classe : exclue avant même d'être
évaluée sur sa pertinence, sans aucune erreur.

Ces tests ne couvrent que la fonction pure (`compute_quality_score`), pas
l'endpoint complet — écrire un test d'intégration sur `POST /admin/offers`
nécessiterait une base Postgres réelle, absente de l'environnement de test
unitaire de ce projet (seul tests/integration/ en dispose).
"""

from app.services.scraping.pipeline import compute_quality_score


class TestAdminOfferGetsRealScore:
    def test_titre_et_description_seuls_produisent_un_score_positif(self):
        """C'est exactement ce que fournit le formulaire admin — jamais 0/None."""
        score = compute_quality_score(
            title="Bourse d'excellence Fondation X",
            description="Bourse ouverte aux étudiants ivoiriens en master, "
                         "couvrant les frais de scolarité et une allocation mensuelle.",
        )
        assert score > 0
        assert score is not None

    def test_titre_seul_reste_positif(self):
        """Même un formulaire minimal (titre uniquement) sort du régime NULL."""
        score = compute_quality_score(title="Stage développeur web")
        assert score > 0

    def test_champs_complets_do_minent_un_score_plus_haut(self):
        """Une offre admin bien renseignée ne doit pas être pénalisée face au scraping."""
        minimal = compute_quality_score(title="Offre")
        complete = compute_quality_score(
            title="Offre complète",
            description="Description détaillée de plus de cinquante caractères pour le score.",
            url="https://exemple.org/offre",
            location="Abidjan",
            company="Yalna",
            posted_at="2026-08-01",
            expires_at="2026-09-01",
        )
        assert complete > minimal

    def test_ne_retourne_jamais_none(self):
        """Le défaut corrigé : la valeur ne doit plus jamais être None après création."""
        assert compute_quality_score(title="x") is not None
