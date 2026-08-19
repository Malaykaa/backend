"""L'agent doit choisir lui-même quelles offres candidates afficher.

`ScrapedOfferService` ne filtre les candidates que par catégorie + pays — la
pertinence au message et à l'objectif du user n'est jugée qu'ensuite, par le
LLM, via la clé `offers` du bloc @@META@@. Ces tests verrouillent ce contrat :
une offre non sélectionnée ne doit jamais atteindre le client, et l'absence
de la clé ne doit jamais retomber sur "tout montrer".
"""

import uuid

from app.agents.base import AgentContext, AgentResponse, SpecializedAgent, _parse_meta_block


class _DummyAgent:
    """Isole `_inject_offers` sans dépendre d'un provider LLM réel."""

    _inject_offers = SpecializedAgent._inject_offers


_OFFER_A = {"offer_ref": "scraped:a", "title": "Offre A"}
_OFFER_B = {"offer_ref": "scraped:b", "title": "Offre B"}


def _ctx_with_offers(offers):
    return AgentContext(
        user_id=uuid.uuid4(), message="m", goal_context={"relevant_offers": offers},
    )


class TestParseMetaBlockOffers:
    def test_extrait_les_refs_selectionnees(self):
        raw = "Texte.\n\n@@META@@\noffers: scraped:a | scraped:b\n@@END@@"
        _, meta = _parse_meta_block(raw)
        assert meta["offer_refs"] == ["scraped:a", "scraped:b"]

    def test_absence_de_cle_offers(self):
        raw = "Texte.\n\n@@META@@\nsteps: Étape 1\n@@END@@"
        _, meta = _parse_meta_block(raw)
        assert "offer_refs" not in meta

    def test_absence_totale_de_bloc_meta(self):
        raw = "Texte simple sans bloc méta."
        _, meta = _parse_meta_block(raw)
        assert "offer_refs" not in meta


class TestInjectOffersCuration:
    def test_seule_l_offre_selectionnee_est_montree(self):
        agent = _DummyAgent()
        ctx = _ctx_with_offers([_OFFER_A, _OFFER_B])
        response = agent._inject_offers(AgentResponse(explanation="x", agent_id="test"), ctx, ["scraped:a"])
        assert [o.offer_ref for o in response.offers] == ["scraped:a"]

    def test_aucune_selection_ne_montre_rien(self):
        """Clé `offers` absente (None) → on ne montre rien par défaut, on ne
        retombe jamais sur le pool DB brut (c'était le bug corrigé)."""
        agent = _DummyAgent()
        ctx = _ctx_with_offers([_OFFER_A, _OFFER_B])
        response = agent._inject_offers(AgentResponse(explanation="x", agent_id="test"), ctx, None)
        assert response.offers == []

    def test_selection_vide_ne_montre_rien(self):
        agent = _DummyAgent()
        ctx = _ctx_with_offers([_OFFER_A, _OFFER_B])
        response = agent._inject_offers(AgentResponse(explanation="x", agent_id="test"), ctx, [])
        assert response.offers == []

    def test_ref_inconnue_est_ignoree(self):
        agent = _DummyAgent()
        ctx = _ctx_with_offers([_OFFER_A])
        response = agent._inject_offers(AgentResponse(explanation="x", agent_id="test"), ctx, ["scraped:inexistant"])
        assert response.offers == []

    def test_pas_de_candidates_du_tout(self):
        agent = _DummyAgent()
        ctx = _ctx_with_offers([])
        response = agent._inject_offers(AgentResponse(explanation="x", agent_id="test"), ctx, ["scraped:a"])
        assert response.offers == []
