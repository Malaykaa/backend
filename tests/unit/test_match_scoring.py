"""Tests de l'échelle commune de matching (`match_score`) et du classement lexical.

Couvre les quatre correctifs de la phase 1 :
- P1 : score unique normalisé 0–100, seuil de notification atteignable
- P3 : classement du repli lexical par couverture réelle des termes
- P8 : frontières de mot sur les bonus domaine / niveau / mots-clés
"""

from types import SimpleNamespace

import pytest

from app.services.match_runner import (
    NOTIFY_THRESHOLD,
    _match_score_of,
)
from app.services.scraped_offer_service import (
    MATCH_MODE_LEXICAL,
    MATCH_MODE_SEMANTIC,
    _PROFILE_MAX,
    _contains_word,
    _profile_score,
    _serialize,
    _term_coverage,
)


def _offer(title="", description="", normalized_title=None, location=None):
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        title=title,
        normalized_title=normalized_title,
        description=description,
        location=location,
        url=None,
        company=None,
        offer_type=None,
        scraped_at=None,
    )


def _intent(domain=None, level=None, location=None, keywords=None):
    return SimpleNamespace(
        domain=domain, level=level, location=location, keywords=keywords or []
    )


# ── P8 — frontières de mot ────────────────────────────────────────────────


class TestWordBoundaries:
    @pytest.mark.parametrize(
        "haystack,needle",
        [
            ("rejoindre une start-up prometteuse", "art"),
            ("un solide partenariat régional", "art"),
            ("attention aux biais cognitifs", "ia"),
            ("poste de directeur", "recteur"),
        ],
    )
    def test_rejette_les_sous_chaines(self, haystack, needle):
        """Le coeur de P8 : `in` produisait ces faux positifs."""
        assert needle in haystack  # l'ancien comportement matchait
        assert _contains_word(haystack, needle) is False

    @pytest.mark.parametrize(
        "haystack,needle",
        [
            ("galerie d'art contemporain", "art"),
            ("ingenieur ia junior", "ia"),
            ("data science et machine learning", "machine learning"),
            ("poste avec ponctuation, art.", "art"),
        ],
    )
    def test_accepte_les_mots_entiers(self, haystack, needle):
        assert _contains_word(haystack, needle) is True

    def test_termes_non_alphanumeriques_restent_trouvables(self):
        """`c++` ne doit pas devenir introuvable à cause d'un \\b mal placé."""
        assert _contains_word("developpeur c++ senior", "c++") is True
        assert _contains_word("stack .net et azure", ".net") is True

    def test_domaine_court_ne_gonfle_plus_le_score(self):
        offer = _offer(title="Stage dans une start-up fintech")
        assert _profile_score(offer, _intent(domain="art")) == 0.0


# ── P3 — couverture lexicale ──────────────────────────────────────────────


class TestTermCoverage:
    def test_couverture_totale(self):
        offer = _offer(title="Bourse master informatique")
        assert _term_coverage(offer, ["bourse", "master", "informatique"]) == 1.0

    def test_couverture_partielle_est_proportionnelle(self):
        offer = _offer(title="Bourse master")
        cov = _term_coverage(offer, ["bourse", "master", "informatique", "canada"])
        assert cov == pytest.approx(0.5)

    def test_le_titre_pese_plus_que_la_description(self):
        dans_titre = _offer(title="Ingenieur data", description="")
        dans_desc = _offer(title="Offre", description="poste d'ingenieur data")
        assert _term_coverage(dans_titre, ["ingenieur", "data"]) > _term_coverage(
            dans_desc, ["ingenieur", "data"]
        )

    def test_normalized_title_est_pris_en_compte(self):
        offer = _offer(title="", normalized_title="bourse doctorale")
        assert _term_coverage(offer, ["bourse"]) == 1.0

    def test_aucun_terme_exploitable(self):
        """Termes trop courts → 0, le score reposera sur le profil seul."""
        assert _term_coverage(_offer(title="Bourse"), ["a", "de", ""]) == 0.0
        assert _term_coverage(_offer(title="Bourse"), []) == 0.0

    def test_une_offre_couvrant_tout_depasse_celle_qui_couvre_un_terme(self):
        """Le classement que P3 rendait impossible."""
        complete = _offer(title="Bourse master informatique Canada")
        partielle = _offer(title="Bourse de cuisine")
        terms = ["bourse", "master", "informatique", "canada"]
        assert _term_coverage(complete, terms) > _term_coverage(partielle, terms)


# ── P1 — échelle commune ──────────────────────────────────────────────────


class TestCommonScale:
    def test_profile_max_correspond_au_plafond_reel(self):
        """domaine 15 + niveau 5 + localisation 5 — les bonus mots-clés sont exclusifs."""
        offer = _offer(
            title="Bourse master informatique",
            description="niveau master",
            location="cote d'ivoire",
        )
        intent = _intent(
            domain="informatique", level="master", location="cote d'ivoire"
        )
        assert _profile_score(offer, intent) == _PROFILE_MAX

    def test_serialize_expose_les_deux_familles_de_score(self):
        payload = _serialize(
            _offer(title="X"), 12.5, match_score=73.4, match_mode=MATCH_MODE_LEXICAL
        )
        assert payload["relevance_score"] == 12.5   # brut, inchangé
        assert payload["match_score"] == 73.4       # normalisé
        assert payload["match_mode"] == MATCH_MODE_LEXICAL

    def test_match_score_est_borne(self):
        assert _serialize(_offer(), 0, match_score=140.0, match_mode="x")["match_score"] == 100.0
        assert _serialize(_offer(), 0, match_score=-8.0, match_mode="x")["match_score"] == 0.0

    def test_relevance_score_reste_intact(self):
        """Les recommandations font de l'arithmétique absolue dessus."""
        payload = _serialize(_offer(), 41.7, match_score=10.0, match_mode="x")
        assert payload["relevance_score"] == 41.7


# ── P1 — le seuil de notification redevient atteignable ───────────────────


class TestNotificationThreshold:
    def test_le_seuil_lexical_est_atteignable(self):
        """Regression P1 : l'ancien seuil (60) dépassait le maximum lexical (25)."""
        excellent = {
            "match_score": 100.0 * (2 / 3 * 1.0 + 1 / 3 * 1.0),
            "match_mode": MATCH_MODE_LEXICAL,
        }
        assert _match_score_of(excellent) >= NOTIFY_THRESHOLD[MATCH_MODE_LEXICAL]

    def test_une_correspondance_mediocre_ne_notifie_pas(self):
        mediocre = {"match_score": 40.0, "match_mode": MATCH_MODE_LEXICAL}
        assert _match_score_of(mediocre) < NOTIFY_THRESHOLD[MATCH_MODE_LEXICAL]

    def test_le_lexical_exige_moins_que_le_semantique(self):
        """Le repli porte moins d'information : même exigence = mode muet."""
        assert (
            NOTIFY_THRESHOLD[MATCH_MODE_LEXICAL]
            < NOTIFY_THRESHOLD[MATCH_MODE_SEMANTIC]
        )

    def test_retrocompatibilite_des_dicts_sans_match_score(self):
        """Un dict à l'ancienne est reprojeté depuis l'échelle sémantique 0–75."""
        assert _match_score_of({"relevance_score": 75.0}) == pytest.approx(100.0)
        assert _match_score_of({"relevance_score": 37.5}) == pytest.approx(50.0)
        assert _match_score_of({}) == 0.0
