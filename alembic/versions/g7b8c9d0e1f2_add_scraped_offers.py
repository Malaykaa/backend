"""Add scraped_offers table for Apify/Perplexity scraping.

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-18 00:00:00.000000
"""

from alembic import op

revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Création idempotente du type ENUM — résiste au re-run et aux rollbacks partiels.
    # Contourne le conflit créé par l'import des modèles ORM dans env.py.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE scrapedoffertype AS ENUM (
                'job', 'formation', 'grant', 'scholarship',
                'partnership', 'call_for_applications', 'opportunity', 'resource'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    # Table complète en SQL brut pour éviter que SQLAlchemy n'émette
    # une deuxième CREATE TYPE via son système d'événements before_create.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scraped_offers (
            id          UUID          PRIMARY KEY,
            source      VARCHAR(100)  NOT NULL,
            offer_type  scrapedoffertype NOT NULL,
            external_id VARCHAR(500)  NOT NULL,
            title       VARCHAR(500)  NOT NULL,
            description TEXT,
            url         TEXT,
            company     VARCHAR(300),
            location    VARCHAR(300),
            salary      VARCHAR(200),
            posted_at   TIMESTAMPTZ,
            expires_at  TIMESTAMPTZ,
            scraped_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            normalized_title VARCHAR(300),
            quality_score    FLOAT,
            raw_data    JSONB,
            is_active   BOOLEAN       NOT NULL DEFAULT TRUE,
            CONSTRAINT uq_scraped_offer_source_eid UNIQUE (source, external_id)
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scraped_offers_source "
        "ON scraped_offers(source)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scraped_offers_offer_type "
        "ON scraped_offers(offer_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scraped_offers_normalized_title "
        "ON scraped_offers(normalized_title)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scraped_offers")
    op.execute("DROP TYPE IF EXISTS scrapedoffertype")
