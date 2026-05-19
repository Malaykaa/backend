"""Add pgvector extension + embedding column on scraped_offers + HNSW index.

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-05-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "k2l3m4n5o6p7"
down_revision = "j1k2l3m4n5o6"
branch_labels = None
depends_on = None

# Aligné sur app.models.scraped_offer.EMBEDDING_DIM (Perplexity pplx-embed-v1).
EMBEDDING_DIM = 1536


def upgrade() -> None:
    # 1. Activer l'extension pgvector. Sur RDS / Cloud SQL elle doit avoir
    #    été pré-autorisée par l'administrateur de l'instance.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Ajouter la colonne embedding (NULL autorisé : backfill différé).
    op.add_column(
        "scraped_offers",
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
    )

    # 3. Index HNSW (vector_cosine_ops) — adapté à la cosine similarity
    #    qu'on utilisera côté repo. Les paramètres m=16, ef_construction=64
    #    sont les défauts pgvector 0.5+ — bons pour < 1M vecteurs.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scraped_offers_embedding_hnsw "
        "ON scraped_offers USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_scraped_offers_embedding_hnsw")
    op.drop_column("scraped_offers", "embedding")
    # On ne désactive PAS l'extension pgvector au downgrade : elle peut être
    # utilisée par d'autres tables / migrations futures, et la suppression
    # ferait échouer le downgrade si une autre colonne Vector existe.
