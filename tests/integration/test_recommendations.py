"""Tests d'intégration recommandations — propositions personnalisées.

Couvre le pipeline /recommendations/propositions : intent + offres scrapées
→ scoring (fraîcheur, niveau, feedback, LLM-cache) → tri → top N.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_propositions_empty_without_intent(client, make_user):
    """Sans UserIntent en base → la route renvoie une liste vide (pas 500)."""
    _, token = make_user()
    auth = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/recommendations/propositions", headers=auth)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_propositions_with_intent_and_offer(client, db_session, make_user):
    """UserIntent + ScrapedOffer en base → la route retourne au moins l'offre matchée.

    On crée un thread minimal, une intent (BTP / stage / Sénégal), et une
    offre scrapée alignée. La recherche sémantique tombe sur ILIKE en absence
    d'embeddings — donc on injecte les mots-clés dans le titre/description.
    """
    user_id, token = make_user(domain="BTP")
    auth = {"Authorization": f"Bearer {token}"}

    # Thread minimal pour rattacher l'intent (FK NOT NULL)
    from app.models.chat import ChatThread, ThreadStatus
    from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType
    from app.models.user_intent import UserIntent

    thread = ChatThread(
        user_id=user_id,
        title="Stage BTP",
        status=ThreadStatus.open,
    )
    db_session.add(thread)
    db_session.flush()

    intent = UserIntent(
        user_id=user_id,
        thread_id=thread.id,
        intent_summary="Stage PFE en BTP au Sénégal, 6 mois",
        intent_type="stage",
        domain="BTP",
        keywords=["PFE", "BTP", "génie civil"],
        location="Sénégal",
        level="master",
        duration="6 mois",
        message_count_at_extraction=4,
        version=1,
    )
    db_session.add(intent)

    offer = ScrapedOffer(
        external_id=f"test-{uuid.uuid4().hex[:8]}",
        source="test",
        offer_type=ScrapedOfferType.job,  # intent_type="stage" est mappé → "job"
        title="Stage PFE BTP — Génie civil Dakar",
        company="ACME Construction",
        location="Dakar, Sénégal",
        description="Stage de fin d'études en BTP / génie civil. Master.",
        url="https://example.test/stage-1",
        scraped_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)
    db_session.commit()

    resp = await client.get("/recommendations/propositions", headers=auth)
    assert resp.status_code == 200, resp.text

    items = resp.json()
    assert isinstance(items, list)
    # ILIKE matche sur "BTP" → on doit retrouver notre offre
    assert any(item["title"] == offer.title for item in items), items

    # Format des items
    matched = next(item for item in items if item["title"] == offer.title)
    assert matched["url"] == offer.url
    assert "score" in matched and matched["score"] >= 0
    assert matched["source"] == "scraped"


@pytest.mark.asyncio
async def test_propositions_unauthenticated_returns_401(client):
    resp = await client.get("/recommendations/propositions")
    assert resp.status_code == 401
