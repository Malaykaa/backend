"""Tests unitaires Jour 9 — Opportunités et scoring."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.main import app
from app.models.opportunity import OpportunityType, UserOpportunityStatus
from app.services.opportunity_service import OpportunityService


# ── Helpers ──────────────────────────────────────────────


def _make_user():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "opp@test.com"
    user.role = "b2c"
    user.is_active = True
    user.created_at = "2026-01-01T00:00:00+00:00"

    profile = MagicMock()
    profile.first_name = "Alice"
    profile.last_name = "Diallo"
    profile.country = "Sénégal"
    profile.domain = "Informatique"
    profile.current_status = "Développeuse"
    profile.skills = None
    profile.cv_url = None
    user.profile = profile
    return user


def _make_opportunity(opp_type="job", domain="Informatique", country="Sénégal"):
    opp = MagicMock()
    opp.id = uuid.uuid4()
    opp.type = OpportunityType(opp_type)
    opp.title = f"Opportunité {opp_type}"
    opp.description = "Description test"
    opp.source_url = None
    opp.deadline = None
    opp.country = country
    opp.domain = domain
    opp.is_active = True
    opp.created_at = "2026-04-02T00:00:00+00:00"
    return opp


def _make_profile(country="Sénégal", domain="Informatique", status="Développeuse"):
    profile = MagicMock()
    profile.country = country
    profile.domain = domain
    profile.current_status = status
    return profile


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_user():
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.clear()


# ── Scoring Algorithm ────────────────────────────────────


class TestScoring:
    def test_full_match(self):
        """Domain + country + status match → score élevé."""
        opp = _make_opportunity("job", "Informatique", "Sénégal")
        profile = _make_profile("Sénégal", "Informatique", "Développeuse")
        score = OpportunityService._compute_score(opp, profile)
        # domain=40 + country=30 + status(développeuse→job)=20 + base=10 = 100
        assert score == 100.0

    def test_domain_match_only(self):
        opp = _make_opportunity("scholarship", "Informatique", "France")
        profile = _make_profile("Sénégal", "Informatique", "Manager")
        score = OpportunityService._compute_score(opp, profile)
        # domain=40 + country=0 + status=0 + base=10 = 50
        assert score == 50.0

    def test_country_match_only(self):
        opp = _make_opportunity("tender", "Agriculture", "Sénégal")
        profile = _make_profile("Sénégal", "Informatique", "Manager")
        score = OpportunityService._compute_score(opp, profile)
        # domain=0 + country=30 + status=0 + base=10 = 40
        assert score == 40.0

    def test_no_match(self):
        opp = _make_opportunity("grant", "Agronomie", "Belgique")
        profile = _make_profile("Sénégal", "Informatique", "Manager")
        score = OpportunityService._compute_score(opp, profile)
        # base=10 only
        assert score == 10.0

    def test_no_profile(self):
        opp = _make_opportunity()
        score = OpportunityService._compute_score(opp, None)
        assert score == 10.0

    def test_partial_domain_match(self):
        """Domain partiel (inclusion) → 25 points."""
        opp = _make_opportunity("job", "Data Science", "Sénégal")
        profile = _make_profile("Sénégal", "Data", "Développeuse")
        score = OpportunityService._compute_score(opp, profile)
        # domain partial=25 + country=30 + status(développeuse→job)=20 + base=10 = 85
        assert score == 85.0

    def test_international_country(self):
        """Pays 'International' → 15 points au lieu de 30."""
        opp = _make_opportunity("grant", "Informatique", "International")
        profile = _make_profile("Sénégal", "Informatique", "Développeuse")
        score = OpportunityService._compute_score(opp, profile)
        # domain=40 + country_intl=15 + base=10 = 65
        assert score == 65.0

    def test_student_scholarship_match(self):
        """Étudiante + scholarship → status match."""
        opp = _make_opportunity("scholarship", "Sciences", "France")
        profile = _make_profile("Sénégal", "Sciences", "Étudiante en master")
        score = OpportunityService._compute_score(opp, profile)
        # domain=40 + country=0 + status=20 + base=10 = 70
        assert score == 70.0

    def test_score_capped_at_100(self):
        score = OpportunityService._compute_score(
            _make_opportunity("job", "Informatique", "Sénégal"),
            _make_profile("Sénégal", "Informatique", "Développeuse"),
        )
        assert score <= 100.0


# ── Router GET /opportunities ────────────────────────────


class TestListOpportunities:
    def test_list_matched(self, client: TestClient, auth_user):
        with patch("app.services.opportunity_service.OpportunityRepository") as MockRepo:
            mock_repo = MockRepo.return_value

            opp1 = _make_opportunity("job", "Informatique", "Sénégal")
            mock_repo.get_active.return_value = [opp1]

            match = MagicMock()
            match.status = UserOpportunityStatus.new
            mock_repo.upsert_match.return_value = match

            resp = client.get("/opportunities")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["score"] > 0
        assert data[0]["opportunity"]["type"] == "job"

    def test_list_no_auth(self, client: TestClient):
        resp = client.get("/opportunities")
        assert resp.status_code == 401

    def test_list_empty(self, client: TestClient, auth_user):
        with patch("app.services.opportunity_service.OpportunityRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_active.return_value = []

            resp = client.get("/opportunities")

        assert resp.status_code == 200
        assert resp.json() == []


# ── Seed data ────────────────────────────────────────────


class TestSeedData:
    def test_seed_list_has_entries(self):
        from scripts.seed_opportunities import OPPORTUNITIES
        assert len(OPPORTUNITIES) >= 40
        types = {o["type"] for o in OPPORTUNITIES}
        assert "job" in types
        assert "scholarship" in types
        assert "internship" in types
        assert "grant" in types
        assert "funding" in types
        assert "tender" in types
