"""Tests d'intégration auth — flux OTP complet (send → verify → me)."""

from __future__ import annotations

import uuid

import pytest

from app.core.cookies import ACCESS_COOKIE


@pytest.mark.asyncio
async def test_send_otp_returns_ok(client):
    """POST /auth/send-otp avec un numéro valide → {ok: true}.

    En env local sans OneMessage/Twilio configurés, le service log juste l'OTP.
    On valide ici la couche HTTP + validation Pydantic.
    """
    resp = await client.post("/auth/send-otp", json={"phone": "+221770000001"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_send_otp_invalid_phone_returns_422(client):
    resp = await client.post("/auth/send-otp", json={"phone": "123"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_via_otp_then_me(client):
    """OTP_MOCK_ACCEPT_ANY=true → tout code à 6 chiffres est accepté.

    Flux : send-otp → verify-otp-register (création compte) → /auth/me (cookie session).
    """
    phone = f"+22177{uuid.uuid4().int % 10_000_000:07d}"

    send = await client.post("/auth/send-otp", json={"phone": phone})
    assert send.status_code == 200

    register = await client.post(
        "/auth/verify-otp-register",
        json={
            "phone": phone,
            "code": "123456",
            "password": "securepass8",
            "first_name": "Aïcha",
            "country": "Sénégal",
        },
    )
    assert register.status_code == 201, register.text
    body = register.json()
    assert "accessToken" in body
    assert body["user"]["id"]
    assert ACCESS_COOKIE in register.cookies

    # /auth/me avec le cookie posé
    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == body["user"]["id"]


@pytest.mark.asyncio
async def test_login_with_email_password(client, make_user):
    """Sanity-check du flux email/password (option secondaire)."""
    suffix = uuid.uuid4().hex[:8]
    email = f"login_{suffix}@example.com"
    make_user(email=email)

    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == email


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client, make_user):
    suffix = uuid.uuid4().hex[:8]
    email = f"wrong_{suffix}@example.com"
    make_user(email=email)

    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "wrongpass1"},
    )
    assert resp.status_code == 401
