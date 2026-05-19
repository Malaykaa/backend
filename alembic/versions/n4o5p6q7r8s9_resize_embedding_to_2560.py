"""Resize scraped_offers.embedding 3416 → 2560 dims (pplx-embed-v1-4b réel).

Le modèle pplx-embed-v1-4b retourne en réalité 2560 dimensions encodées
en int8 quantifié (base64 binaire), pas 3416 float32 comme supposé initialement.

Vérifié expérimentalement :
    base64 → 2560 octets → struct.unpack('2560b') → int8 signés [-128, 127]
    normalisation : /128.0 → floats [-1.0, 1.0]
    cosine(bourse, scholarship) = 0.84  ✓
    cosine(bourse, cuisine)     = 0.32  ✓  (bonne discrimination sémantique)

On drop+recreate la colonne : les vecteurs 3416 (tous NULL de toute façon
puisque le décodage était cassé) sont incompatibles avec Vector(2560).

Note index HNSW : 2560 > 2000 dims → toujours hors limite pour Vector.
Pour indexer à cette dimension, il faudrait passer à halfvec(2560) avec
pgvector ≥ 0.7. Acceptable en seq scan au volume actuel (< quelques milliers).

Revision ID: n4o5p6q7r8s9
Revises: m4n5o6p7q8r9
Create Date: 2026-05-16 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "n4o5p6q7r8s9"
down_revision = "m4n5o6p7q8r9"
branch_labels = None
depends_on = None

NEW_DIM = 2560
OLD_DIM = 3416


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_scraped_offers_embedding_hnsw")
    op.drop_column("scraped_offers", "embedding")
    op.add_column(
        "scraped_offers",
        sa.Column("embedding", Vector(NEW_DIM), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scraped_offers", "embedding")
    op.add_column(
        "scraped_offers",
        sa.Column("embedding", Vector(OLD_DIM), nullable=True),
    )
