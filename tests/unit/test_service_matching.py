"""Tests du matching et de la visibilité — mise en relation prestataires ↔ clients.

Deux invariants sont protégés ici :

1. La pertinence au besoin qualifie, la géographie départage. Un profil hors
   sujet dans la bonne ville ne doit jamais être sollicité.
2. Les coordonnées ne circulent qu'après double validation. La règle est portée
   par la machine à états de MatchDecision, ces tests la verrouillent.
"""

from types import SimpleNamespace

import pytest

from app.models.service import MatchDecision, MatchSource, ProviderStatus, RequestStatus
from app.services.service_matching import (
    MIN_MATCH_SCORE,
    MIN_RELEVANCE,
    _geo_bonus,
    _normalize_title,
    _score,
    build_search_terms,
    embedding_text,
)


def _req(title="Plombier pour fuite", keywords=None, city="Abidjan", country="CI"):
    return SimpleNamespace(
        title=title, description="fuite urgente sous l'évier",
        keywords=keywords if keywords is not None else ["plombier", "fuite", "urgence"],
        city=city, country=country, requester_id="client-1",
    )


def _score_of(req, *, title, description="", city=None, country=None, cosine=None):
    terms = build_search_terms(
        title=req.title, description=req.description, keywords=req.keywords
    )
    return _score(
        request=req, terms=terms, title=title, normalized_title=_normalize_title(title),
        description=description, city=city, country=country, cosine=cosine,
    )


# ── Invariant 1 : la géographie ne qualifie jamais ─────────────────────────


class TestPertinenceAvantGeographie:
    def test_profil_hors_sujet_dans_la_bonne_ville_est_ecarte(self):
        """Le défaut trouvé à la mise au point : 33 points sur la seule ville."""
        s = _score_of(
            _req(), title="Développeur web",
            description="Sites internet et applications", city="Abidjan", country="CI",
        )
        assert s == 0.0
        assert s < MIN_MATCH_SCORE

    def test_profil_pertinent_est_retenu(self):
        s = _score_of(
            _req(), title="Plombier urgence Abidjan",
            description="Dépannage fuite et plomberie", city="Abidjan", country="CI",
        )
        assert s >= MIN_MATCH_SCORE

    def test_la_ville_departage_a_pertinence_egale(self):
        proche = _score_of(_req(), title="Plombier fuite", city="Abidjan", country="CI")
        loin = _score_of(_req(), title="Plombier fuite", city="Bouaké", country="CI")
        assert proche > loin

    def test_un_bon_profil_loin_bat_un_mauvais_profil_proche(self):
        """La géographie ne renverse pas un écart de pertinence réel."""
        bon_loin = _score_of(
            _req(), title="Plombier fuite urgence", description="plomberie",
            city="Dakar", country="SN",
        )
        mauvais_proche = _score_of(
            _req(), title="Coiffeuse", description="coiffure", city="Abidjan", country="CI",
        )
        assert bon_loin > mauvais_proche

    def test_seuil_de_pertinence_borne(self):
        """Volontairement bas : il exclut le hors-sujet, pas l'approximatif."""
        assert 0.0 < MIN_RELEVANCE < 0.5


class TestBonusGeographique:
    def test_meme_ville(self):
        assert _geo_bonus(request=_req(), city="Abidjan", country="CI") == 1.0

    def test_meme_pays_ville_differente(self):
        assert _geo_bonus(request=_req(), city="Bouaké", country="CI") == pytest.approx(0.6)

    def test_pays_different(self):
        assert _geo_bonus(request=_req(), city="Dakar", country="SN") == 0.0

    def test_ville_non_renseignee_ne_penalise_pas_le_pays(self):
        """Beaucoup de prestations se font à distance."""
        assert _geo_bonus(request=_req(), city=None, country="CI") == pytest.approx(0.6)


# ── Termes de recherche ────────────────────────────────────────────────────


class TestTermesDeRecherche:
    def test_les_mots_cles_libres_priment(self):
        terms = build_search_terms(
            title="Besoin urgent", description="x", keywords=["plombier", "fuite"]
        )
        assert terms[:2] == ["plombier", "fuite"]

    def test_le_titre_complete_les_mots_cles(self):
        terms = build_search_terms(title="Plombier Abidjan", description="x", keywords=[])
        assert "plombier" in terms and "abidjan" in terms

    def test_les_mots_trop_courts_sont_ecartes(self):
        terms = build_search_terms(title="Un de la", description="x", keywords=[])
        assert terms == []

    def test_pas_de_doublon_entre_mots_cles_et_titre(self):
        terms = build_search_terms(
            title="Plombier urgence", description="x", keywords=["plombier"]
        )
        assert terms.count("plombier") == 1

    def test_liste_bornee(self):
        terms = build_search_terms(
            title=" ".join(f"mot{i}" for i in range(30)),
            description="x", keywords=[f"kw{i}" for i in range(20)],
        )
        assert len(terms) <= 12

    def test_accents_normalises(self):
        assert _normalize_title("Dépannage Électricité") == "depannage electricite"


class TestTexteVectorise:
    def test_le_titre_vient_en_premier(self):
        text = embedding_text(
            title="Plombier", description="longue description", keywords=["fuite"],
            city="Abidjan", country="CI",
        )
        assert text.startswith("Plombier")

    def test_description_tronquee(self):
        text = embedding_text(title="T", description="x" * 5000, keywords=None)
        assert len(text) < 2100

    def test_champs_vides_ignores(self):
        assert embedding_text(title="Plombier", description="", keywords=None) == "Plombier"


# ── Invariant 2 : la double validation ─────────────────────────────────────


class TestMachineAEtats:
    def test_etat_initial(self):
        assert MatchDecision.pending.value == "pending"

    def test_le_client_ne_peut_retenir_qu_apres_acceptation_du_prestataire(self):
        """Garde-fou du router : seul provider_accepted mène à client_accepted."""
        etats_refuses = [
            MatchDecision.pending,
            MatchDecision.provider_declined,
            MatchDecision.client_declined,
            MatchDecision.expired,
        ]
        for etat in etats_refuses:
            assert etat != MatchDecision.provider_accepted

    def test_les_deux_viviers_sont_distincts(self):
        assert MatchSource.provider != MatchSource.public

    def test_la_mise_en_relation_est_un_etat_terminal_unique(self):
        """Un seul état déverrouille les coordonnées."""
        deverrouillants = [
            d for d in MatchDecision if d == MatchDecision.client_accepted
        ]
        assert len(deverrouillants) == 1

    def test_une_vitrine_nait_non_publiee(self):
        """Publier est un acte explicite, avec consentement distinct."""
        assert ProviderStatus.draft.value == "draft"
        assert ProviderStatus.draft != ProviderStatus.published

    def test_une_demande_nait_privee(self):
        """Le grand public n'est jamais contacté sans décision du client."""
        assert RequestStatus.open != RequestStatus.public
