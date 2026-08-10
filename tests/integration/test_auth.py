"""Tests d'intégration auth — flux téléphone sans OTP (register-phone → me)."""

from __future__ import annotations

import uuid

import pytest

from app.core.cookies import ACCESS_COOKIE


@pytest.mark.asyncio
async def test_register_phone_invalid_phone_returns_422(client):
    resp = await client.post(
        "/auth/register-phone", json={"phone": "123", "password": "securepass8"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_phone_then_me(client):
    """Flux : register-phone (création compte directe, sans OTP) → /auth/me (cookie session)."""
    phone = f"+22177{uuid.uuid4().int % 10_000_000:07d}"

    register = await client.post(
        "/auth/register-phone",
        json={
            "phone": phone,
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
async def test_register_phone_duplicate_returns_409(client):
    phone = f"+22177{uuid.uuid4().int % 10_000_000:07d}"

    first = await client.post(
        "/auth/register-phone", json={"phone": phone, "password": "securepass8"}
    )
    assert first.status_code == 201

    second = await client.post(
        "/auth/register-phone", json={"phone": phone, "password": "anotherpass8"}
    )
    assert second.status_code == 409


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
