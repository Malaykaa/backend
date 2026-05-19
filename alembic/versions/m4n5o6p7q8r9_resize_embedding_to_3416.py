"""Resize scraped_offers.embedding to 3416 dims (Perplexity pplx-embed-v1-4b).

Le modèle pplx-embed-v1-4b retourne 3416 dimensions natives. La colonne
était initialement dimensionnée pour pplx-embed-v1 (1536). On drop +
recreate la colonne : les vecteurs existants sont incompatibles et seront
ré-générés via scripts/backfill_embeddings.py.

Limitation pgvector : l'index HNSW (et IVF) plafonne à 2000 dims pour le
type `Vector`. À 3416 dims on perd l'index → seq scan sur la cosine.
Acceptable au volume actuel (< quelques milliers d'offres). Pour scaler :
upgrader pgvector ≥ 0.7 et passer à `halfvec(3416)` qui accepte jusqu'à
4000 dims indexées.

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-05-08 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "m4n5o6p7q8r9"
down_revision = "l3m4n5o6p7q8"
branch_labels = None
depends_on = None

NEW_DIM = 3416
OLD_DIM = 1536


def upgrade() -> None:
    # Drop l'ancien index HNSW (créé en migration k2l3m4n5o6p7).
    op.execute("DROP INDEX IF EXISTS ix_scraped_offers_embedding_hnsw")
    # Drop+recreate la colonne (les vecteurs 1536 sont incompatibles).
    op.drop_column("scraped_offers", "embedding")
    op.add_column(
        "scraped_offers",
        sa.Column("embedding", Vector(NEW_DIM), nullable=True),
    )
    # Pas d'index HNSW : > 2000 dims n'est pas supporté pour le type Vector.


def downgrade() -> None:
    op.drop_column("scraped_offers", "embedding")
    op.add_column(
        "scraped_offers",
        sa.Column("embedding", Vector(OLD_DIM), nullable=True),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scraped_offers_embedding_hnsw "
        "ON scraped_offers USING hnsw (embedding vector_cosine_ops)"
    )
