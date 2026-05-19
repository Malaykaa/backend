"""Tests d'intégration chat — création thread + envoi message + streaming SSE."""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_create_thread_and_send_message(client, make_user):
    """POST /chat/threads (sans preset) → POST messages → réponse non-stream."""
    _, token = make_user()
    auth = {"Authorization": f"Bearer {token}"}

    # 1. Créer un thread simple
    create = await client.post(
        "/chat/threads",
        json={"title": "Test thread"},
        headers=auth,
    )
    assert create.status_code == 201, create.text
    thread = create.json()
    assert thread["id"]
    thread_id = thread["id"]

    # 2. Envoyer un message — l'agent répond en mode mock (déterministe)
    resp = await client.post(
        f"/chat/threads/{thread_id}/messages",
        json={"content": "Je prépare le bac, peux-tu m'aider ?", "useAi": True},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "content" in body
    assert isinstance(body["content"], str) and body["content"]
    assert body["llmUsed"] is True


@pytest.mark.asyncio
async def test_stream_message_emits_done_event(client, make_user):
    """POST /chat/threads/{id}/stream → flux SSE qui se termine par 'event: done'."""
    _, token = make_user()
    auth = {"Authorization": f"Bearer {token}"}

    create = await client.post(
        "/chat/threads",
        json={"title": "Stream test"},
        headers=auth,
    )
    assert create.status_code == 201
    thread_id = create.json()["id"]

    async with client.stream(
        "POST",
        f"/chat/threads/{thread_id}/stream",
        json={"content": "Bonjour", "useAi": True},
        headers=auth,
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events: list[dict] = []
        current_event: str | None = None
        async for raw in resp.aiter_lines():
            if not raw:
                continue
            if raw.startswith("event:"):
                current_event = raw.split(":", 1)[1].strip()
            elif raw.startswith("data:"):
                payload = raw.split(":", 1)[1].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {"raw": payload}
                events.append({"event": current_event, "data": data})
                current_event = None

    assert events, "le stream SSE n'a émis aucun event"
    last = events[-1]
    assert last["event"] == "done"
    assert "content" in last["data"]
