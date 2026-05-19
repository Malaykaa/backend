"""Migrate embedding vector(2560) → halfvec(2560) and create HNSW index.

halfvec (16-bit floats) allows HNSW indexing up to 4000 dims, whereas
vector (32-bit) is capped at 2000 dims.  At 2560 dims we had to do a
linear scan O(n); with this index cosine search becomes O(log n).

Storage benefit: 2560 × 2 bytes = 5 KB/row instead of 10 KB.

Embeddings are nullable and regenerated automatically by the scraping
pipeline, so dropping and re-adding the column is safe — NULL rows are
re-filled on the next scraping run.

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-05-17
"""

from __future__ import annotations

from alembic import op

revision = "o5p6q7r8s9t0"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None

# Embedding dimension — must match app/models/scraped_offer.py EMBEDDING_DIM
_DIM = 2560


def upgrade() -> None:
    # 1. Drop the vector(2560) column (data is regeneratable).
    op.execute("ALTER TABLE scraped_offers DROP COLUMN IF EXISTS embedding")

    # 2. Add halfvec(2560) column — same semantic, half the storage.
    op.execute(f"ALTER TABLE scraped_offers ADD COLUMN embedding halfvec({_DIM})")

    # 3. Create HNSW index with cosine distance operator class.
    #    m=16 (graph connections) and ef_construction=64 (build quality)
    #    are pgvector defaults — good for semantic similarity search.
    op.execute(
        "CREATE INDEX ix_scraped_offers_embedding_hnsw "
        "ON scraped_offers "
        "USING hnsw (embedding halfvec_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_scraped_offers_embedding_hnsw")
    op.execute("ALTER TABLE scraped_offers DROP COLUMN IF EXISTS embedding")
    op.execute(f"ALTER TABLE scraped_offers ADD COLUMN embedding vector({_DIM})")
