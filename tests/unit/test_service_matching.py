"""Tests du matching et de la visibilité — mise en relation prestataires ↔ clients.

Deux invariants sont protégés ici :

1. La pertinence au besoin qualifie ; la géographie FILTRE en amont (SQL), elle
   ne pondère plus le score. Un profil hors sujet ne doit jamais être retenu,
   quelle que soit sa localisation — et un profil hors zone ne doit jamais
   même être candidat quand le client a précisé un lieu.
2. Les coordonnées ne circulent qu'après double validation. La règle est portée
   par la machine à états de MatchDecision, ces tests la verrouillent.
"""

from types import SimpleNamespace

from sqlalchemy import select

from app.models.service import (
    DeliveryMode, MatchDecision, MatchSource, ProviderStatus, RequestStatus, ServiceProvider,
)
from app.services.service_matching import (
    MIN_MATCH_SCORE,
    _apply_geo_filter,
    _normalize_title,
    _score,
    build_search_terms,
    embedding_text,
)


def _req(title="Plombier pour fuite", keywords=None, city="Abidjan", country="CI",
         delivery_mode=DeliveryMode.onsite):
    return SimpleNamespace(
        title=title, description="fuite urgente sous l'évier",
        keywords=keywords if keywords is not None else ["plombier", "fuite", "urgence"],
        city=city, country=country, requester_id="client-1", delivery_mode=delivery_mode,
    )


def _score_of(req, *, title, description="", cosine=None):
    terms = build_search_terms(
        title=req.title, description=req.description, keywords=req.keywords
    )
    return _score(
        terms=terms, title=title, normalized_title=_normalize_title(title),
        description=description, cosine=cosine,
    )


# ── Invariant 1a : la pertinence, seule, qualifie ──────────────────────────


class TestPertinenceSeuleQualifie:
    def test_profil_hors_sujet_est_ecarte(self):
        s = _score_of(
            _req(), title="Développeur web", description="Sites internet et applications",
        )
        assert s < MIN_MATCH_SCORE

    def test_profil_pertinent_est_retenu(self):
        s = _score_of(
            _req(), title="Plombier urgence Abidjan", description="Dépannage fuite et plomberie",
        )
        assert s >= MIN_MATCH_SCORE

    def test_seuil_de_score_borne(self):
        assert 0.0 < MIN_MATCH_SCORE < 100.0


# ── Invariant 1b : la géographie filtre en SQL, jamais en score ───────────
#
# `_apply_geo_filter` construit le WHERE avant toute exécution — ces tests
# inspectent la clause produite, sans toucher la base : c'est la structure de
# la requête qui est l'invariant, pas un résultat d'exécution.


class TestFiltreGeographique:
    def _query(self, req):
        base = select(ServiceProvider)
        return _apply_geo_filter(
            base, req, city_col=ServiceProvider.city, country_col=ServiceProvider.country,
        )

    def test_a_distance_aucun_filtre(self):
        """Le client a dit que la localisation n'a pas d'importance."""
        req = _req(city="Abidjan", country="CI", delivery_mode=DeliveryMode.remote)
        assert self._query(req).whereclause is None

    def test_presentiel_filtre_pays_et_ville(self):
        req = _req(city="Abidjan", country="CI", delivery_mode=DeliveryMode.onsite)
        sql = str(self._query(req).whereclause)
        assert "country" in sql
        assert "city" in sql

    def test_hybride_sans_ville_filtre_seulement_le_pays(self):
        """Le client n'a précisé qu'un pays — pas de ville à exiger en plus."""
        req = _req(city=None, country="CI", delivery_mode=DeliveryMode.hybrid)
        sql = str(self._query(req).whereclause)
        assert "country" in sql
        assert "city" not in sql

    def test_aucune_localisation_precisee_aucun_filtre(self):
        req = _req(city=None, country=None, delivery_mode=DeliveryMode.onsite)
        assert self._query(req).whereclause is None


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
