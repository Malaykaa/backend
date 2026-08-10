"""Tests unitaires pour l'authentification (mock des repos, pas de DB)."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from app.core.deps import get_current_user
from app.core.security import hash_password
from app.main import app


@pytest.fixture()
def client():
    return TestClient(app)


def _make_user(email: str = "test@example.com", password: str = "password123", phone: str | None = None):
    """Crée un faux User avec profil pour les tests."""
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = email
    user.phone = phone
    user.password_hash = hash_password(password)
    user.role = "b2c"
    user.is_active = True
    user.created_at = "2026-01-01T00:00:00+00:00"

    profile = MagicMock()
    profile.first_name = "John"
    profile.last_name = "Doe"
    profile.gender = None
    profile.birth_year = None
    profile.country = None
    profile.primary_role = None
    profile.domain = None
    profile.current_status = None
    profile.skills = None
    profile.cv_url = None

    user.profile = profile
    return user


# ── POST /auth/register ──────────────────────────────────


class TestRegister:
    def test_register_success(self, client: TestClient):
        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_email.return_value = None  # pas de doublon

            user = _make_user("new@example.com")
            mock_repo.create_with_profile.return_value = user

            resp = client.post(
                "/auth/register",
                json={
                    "email": "new@example.com",
                    "password": "securepass8",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["user"]["email"] == "new@example.com"
        assert "accessToken" in data
        assert ACCESS_COOKIE in resp.cookies
        assert REFRESH_COOKIE in resp.cookies

    def test_register_duplicate_email(self, client: TestClient):
        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_email.return_value = _make_user()  # email déjà pris

            resp = client.post(
                "/auth/register",
                json={
                    "email": "test@example.com",
                    "password": "securepass8",
                },
            )

        assert resp.status_code == 409

    def test_register_short_password(self, client: TestClient):
        resp = client.post(
            "/auth/register",
            json={"email": "a@b.com", "password": "short"},
        )
        assert resp.status_code == 422  # validation Pydantic

    def test_register_invalid_email(self, client: TestClient):
        resp = client.post(
            "/auth/register",
            json={"email": "not-an-email", "password": "securepass8"},
        )
        assert resp.status_code == 422


# ── POST /auth/login ─────────────────────────────────────


class TestLogin:
    def test_login_success(self, client: TestClient):
        user = _make_user("login@example.com", "correctpass")

        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_email.return_value = user
            mock_repo.get_with_profile.return_value = user

            resp = client.post(
                "/auth/login",
                json={"email": "login@example.com", "password": "correctpass"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["email"] == "login@example.com"
        assert "accessToken" in data
        assert ACCESS_COOKIE in resp.cookies

    def test_login_wrong_password(self, client: TestClient):
        user = _make_user("login@example.com", "correctpass")

        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_email.return_value = user

            resp = client.post(
                "/auth/login",
                json={"email": "login@example.com", "password": "wrongpass"},
            )

        assert resp.status_code == 401

    def test_login_unknown_email(self, client: TestClient):
        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_email.return_value = None

            resp = client.post(
                "/auth/login",
                json={"email": "nope@example.com", "password": "whatever8"},
            )

        assert resp.status_code == 401


# ── GET /auth/me ──────────────────────────────────────────


class TestMe:
    def test_me_no_cookie(self, client: TestClient):
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_me_with_valid_cookie(self, client: TestClient):
        user = _make_user()

        # Utiliser dependency_overrides (la bonne façon de mocker les deps FastAPI)
        app.dependency_overrides[get_current_user] = lambda: user

        resp = client.get("/auth/me")

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == user.email


# ── POST /auth/logout ────────────────────────────────────


class TestLogout:
    def test_logout(self, client: TestClient):
        resp = client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Déconnecté."


# ── POST /auth/refresh ───────────────────────────────────


class TestRefresh:
    def test_refresh_no_cookie(self, client: TestClient):
        resp = client.post("/auth/refresh")
        assert resp.status_code == 401


# ── POST /auth/register-phone ────────────────────────────


class TestRegisterPhone:
    def test_register_phone_success(self, client: TestClient):
        user = _make_user("2250700000000@malaykaa.app", "Secure1!pass", phone="2250700000000")

        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_phone.return_value = None
            mock_repo.create_with_profile.return_value = user

            resp = client.post(
                "/auth/register-phone",
                json={
                    "phone": "+2250700000000",
                    "password": "Secure1!pass",
                    "first_name": "Amadou",
                    "last_name": "Koné",
                    "country": "CI",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert "accessToken" in data
        assert ACCESS_COOKIE in resp.cookies

    def test_register_phone_invalid_phone(self, client: TestClient):
        resp = client.post(
            "/auth/register-phone",
            json={"phone": "123", "password": "Secure1!pass"},
        )
        assert resp.status_code == 422

    def test_register_phone_underage(self, client: TestClient):
        resp = client.post(
            "/auth/register-phone",
            json={
                "phone": "+2250700000000",
                "password": "Secure1!pass",
                "birth_year": 2020,  # trop jeune
            },
        )
        assert resp.status_code == 422

    def test_register_phone_already_used(self, client: TestClient):
        user = _make_user("2250700000000@malaykaa.app", "Secure1!pass", phone="2250700000000")

        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_phone.return_value = user  # déjà existant

            resp = client.post(
                "/auth/register-phone",
                json={"phone": "+2250700000000", "password": "Secure1!pass"},
            )

        assert resp.status_code == 409


# ── POST /auth/login-phone ───────────────────────────────


class TestLoginPhone:
    def test_login_phone_success(self, client: TestClient):
        user = _make_user("2250700000000@malaykaa.app", "correctpass", phone="2250700000000")

        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_phone.return_value = user
            mock_repo.get_with_profile.return_value = user

            resp = client.post(
                "/auth/login-phone",
                json={"phone": "+2250700000000", "password": "correctpass"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "accessToken" in data
        assert data["user"]["email"] == "2250700000000@malaykaa.app"
        assert ACCESS_COOKIE in resp.cookies

    def test_login_phone_wrong_password(self, client: TestClient):
        user = _make_user("2250700000000@malaykaa.app", "correctpass", phone="2250700000000")

        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_phone.return_value = user

            resp = client.post(
                "/auth/login-phone",
                json={"phone": "+2250700000000", "password": "wrongpass"},
            )

        assert resp.status_code == 401

    def test_login_phone_not_found(self, client: TestClient):
        with patch("app.services.auth_service.UserRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_by_phone.return_value = None

            resp = client.post(
                "/auth/login-phone",
                json={"phone": "+2250700000000", "password": "whatever"},
            )

        assert resp.status_code == 401
