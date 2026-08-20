"""L'agent doit choisir lui-même quelles fiches métiers candidates afficher.

Mirroir de test_offer_curation.py : `CareerReferenceService` ne filtre les
candidates que par pays + mots-clés — la pertinence n'est jugée qu'ensuite,
par le LLM, via la clé `metiers` du bloc @@META@@. Une fiche non sélectionnée
ne doit jamais atteindre le client, et l'absence de la clé ne doit jamais
retomber sur "tout montrer".
"""

import uuid

from app.agents.base import AgentContext, AgentResponse, SpecializedAgent, _parse_meta_block
from app.services.career_reference_service import _keywords, _parse_career_ref


class _DummyAgent:
    """Isole `_inject_careers` sans dépendre d'un provider LLM réel."""

    _inject_careers = SpecializedAgent._inject_careers


_CAREER_A = {"career_ref": "career:a", "title": "Métier A"}
_CAREER_B = {"career_ref": "career:b", "title": "Métier B"}


def _ctx_with_careers(careers):
    return AgentContext(
        user_id=uuid.uuid4(), message="m", goal_context={"relevant_careers": careers},
    )


class TestParseMetaBlockMetiers:
    def test_extrait_les_refs_selectionnees(self):
        raw = "Texte.\n\n@@META@@\nmetiers: career:a | career:b\n@@END@@"
        _, meta = _parse_meta_block(raw)
        assert meta["career_refs"] == ["career:a", "career:b"]

    def test_absence_de_cle_metiers(self):
        raw = "Texte.\n\n@@META@@\nsteps: Étape 1\n@@END@@"
        _, meta = _parse_meta_block(raw)
        assert "career_refs" not in meta

    def test_absence_totale_de_bloc_meta(self):
        raw = "Texte simple sans bloc méta."
        _, meta = _parse_meta_block(raw)
        assert "career_refs" not in meta


class TestInjectCareersCuration:
    def test_seule_la_fiche_selectionnee_est_montree(self):
        agent = _DummyAgent()
        ctx = _ctx_with_careers([_CAREER_A, _CAREER_B])
        response = agent._inject_careers(AgentResponse(explanation="x", agent_id="test"), ctx, ["career:a"])
        assert [c.career_ref for c in response.careers] == ["career:a"]

    def test_aucune_selection_ne_montre_rien(self):
        agent = _DummyAgent()
        ctx = _ctx_with_careers([_CAREER_A, _CAREER_B])
        response = agent._inject_careers(AgentResponse(explanation="x", agent_id="test"), ctx, None)
        assert response.careers == []

    def test_selection_vide_ne_montre_rien(self):
        agent = _DummyAgent()
        ctx = _ctx_with_careers([_CAREER_A, _CAREER_B])
        response = agent._inject_careers(AgentResponse(explanation="x", agent_id="test"), ctx, [])
        assert response.careers == []

    def test_ref_inconnue_est_ignoree(self):
        agent = _DummyAgent()
        ctx = _ctx_with_careers([_CAREER_A])
        response = agent._inject_careers(AgentResponse(explanation="x", agent_id="test"), ctx, ["career:inexistant"])
        assert response.careers == []

    def test_pas_de_candidates_du_tout(self):
        agent = _DummyAgent()
        ctx = _ctx_with_careers([])
        response = agent._inject_careers(AgentResponse(explanation="x", agent_id="test"), ctx, ["career:a"])
        assert response.careers == []


class TestParseCareerRef:
    def test_ref_valide(self):
        cid = uuid.uuid4()
        assert _parse_career_ref(f"career:{cid}") == cid

    def test_prefixe_manquant(self):
        assert _parse_career_ref(str(uuid.uuid4())) is None

    def test_uuid_invalide(self):
        assert _parse_career_ref("career:pas-un-uuid") is None

    def test_chaine_vide(self):
        assert _parse_career_ref("") is None


class TestKeywords:
    def test_extrait_les_mots_significatifs(self):
        words = _keywords("Je veux devenir développeur web à Abidjan")
        assert "développeur" in words
        assert "abidjan" in words
        assert "je" not in words  # trop court (< 4 caractères)

    def test_ignore_les_stopwords(self):
        words = _keywords("je suis dans le informatique")
        assert "informatique" in words
        assert "suis" not in words and "dans" not in words

    def test_texte_vide_ou_none(self):
        assert _keywords(None, "") == []
