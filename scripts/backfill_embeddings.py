"""Backfill embeddings — vectorise toutes les offres scrapées avec embedding NULL.

Usage :
    python -m scripts.backfill_embeddings              # batch=100 par défaut
    python -m scripts.backfill_embeddings --batch 50
    python -m scripts.backfill_embeddings --max 500    # arrêt après N offres

Pré-requis : PERPLEXITY_API_KEY exporté. Sans clé, le script log un warning et sort.
Idempotent : ne re-vectorise pas les offres ayant déjà un embedding.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.core.database import SessionLocal
from app.services.embedding_service import get_embedding_service
from app.services.scraping.pipeline import embed_pending

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_embeddings")


async def run(batch: int, max_offers: int | None) -> int:
    svc = get_embedding_service()
    if not svc.available:
        logger.error(
            "PERPLEXITY_API_KEY absent ou EmbeddingService indisponible — abandon."
        )
        return 1

    total = 0
    db = SessionLocal()
    try:
        while True:
            remaining = None if max_offers is None else max(0, max_offers - total)
            if remaining == 0:
                break
            limit = batch if remaining is None else min(batch, remaining)

            indexed = await embed_pending(db, limit=limit)
            if indexed == 0:
                break
            db.commit()
            total += indexed
            logger.info("Batch indexé : %d (cumul %d)", indexed, total)
    finally:
        db.close()

    logger.info("Backfill terminé — %d offres vectorisées.", total)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=100, help="Taille de batch (défaut 100).")
    parser.add_argument("--max", type=int, default=None, help="Nombre max d'offres à indexer.")
    args = parser.parse_args()

    rc = asyncio.run(run(batch=args.batch, max_offers=args.max))
    sys.exit(rc)


if __name__ == "__main__":
    main()
