"""Add service marketplace tables — providers, requests, matches.

Revision ID: 018
Revises: 017
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import HALFVEC

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

_DIM = 2560


def upgrade() -> None:
    # Les enums sont créés explicitement : les colonnes sont déclarées avec
    # create_constraint=False côté modèle, il faut donc le type PostgreSQL.
    op.execute(
        "DO $$ BEGIN CREATE TYPE provider_status_enum AS ENUM "
        "('draft','published','paused','suspended'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE request_type_enum AS ENUM "
        "('prestation','emploi','stage','autre'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE request_status_enum AS ENUM "
        "('open','public','fulfilled','closed','expired'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE match_decision_enum AS ENUM "
        "('pending','provider_accepted','provider_declined',"
        "'client_accepted','client_declined','expired'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE match_source_enum AS ENUM ('provider','public'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )

    op.create_table(
        "service_providers",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("normalized_title", sa.String(300), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("rate_text", sa.String(200), nullable=True),
        sa.Column("availability_text", sa.String(200), nullable=True),
        sa.Column("years_experience", sa.Integer(), nullable=True),
        sa.Column("contact_phone", sa.String(30), nullable=True,
                  comment="Révélé au client uniquement après double validation."),
        sa.Column("status", postgresql.ENUM(name="provider_status_enum", create_type=False),
                  nullable=False, server_default="draft"),
        sa.Column("consent_public_at", sa.DateTime(timezone=True), nullable=True,
                  comment="Consentement à la publication — distinct de celui d'inscription."),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", HALFVEC(_DIM), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_service_providers_user_id", "service_providers", ["user_id"])
    op.create_index("ix_service_providers_status", "service_providers", ["status"])
    op.create_index("ix_service_providers_status_country", "service_providers", ["status", "country"])

    op.create_table(
        "service_requests",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("requester_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("chat_threads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_type", postgresql.ENUM(name="request_type_enum", create_type=False),
                  nullable=False, server_default="prestation"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("normalized_title", sa.String(300), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("budget_hint", sa.String(200), nullable=True),
        sa.Column("status", postgresql.ENUM(name="request_status_enum", create_type=False),
                  nullable=False, server_default="open"),
        sa.Column("published_public_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", HALFVEC(_DIM), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_service_requests_requester_id", "service_requests", ["requester_id"])
    op.create_index("ix_service_requests_status", "service_requests", ["status"])

    op.create_table(
        "service_request_matches",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("service_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("service_providers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", postgresql.ENUM(name="match_source_enum", create_type=False),
                  nullable=False, server_default="provider"),
        sa.Column("decision", postgresql.ENUM(name="match_decision_enum", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("match_mode", sa.String(20), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("request_id", "user_id", name="uq_service_match_request_user"),
    )
    op.create_index("ix_service_request_matches_request_id", "service_request_matches", ["request_id"])
    op.create_index("ix_service_request_matches_user_id", "service_request_matches", ["user_id"])
    op.create_index("ix_service_request_matches_decision", "service_request_matches", ["decision"])

    # Index vectoriels — mêmes paramètres que scraped_offers (halfvec permet
    # l'indexation HNSW au-delà de 2000 dimensions, contrairement à vector).
    op.execute(
        "CREATE INDEX ix_service_providers_embedding_hnsw ON service_providers "
        "USING hnsw (embedding halfvec_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_service_requests_embedding_hnsw ON service_requests "
        "USING hnsw (embedding halfvec_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_service_requests_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_service_providers_embedding_hnsw")
    op.drop_table("service_request_matches")
    op.drop_table("service_requests")
    op.drop_table("service_providers")
    for enum_name in (
        "match_source_enum", "match_decision_enum",
        "request_status_enum", "request_type_enum", "provider_status_enum",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
