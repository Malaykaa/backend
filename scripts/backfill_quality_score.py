"""Backfill quality_score — recalcule le score qualité des offres qui n'en ont pas.

Corrige les offres créées avant le correctif de POST /admin/offers (celui-ci
appelait auparavant `quality_score=None` sans jamais lancer `process_offer`).
Une offre à NULL passe derrière toute offre ayant un score, même faible, dans
le classement SQL de la recherche par mots-clés (ORDER BY quality_score DESC
NULLS LAST) — au-delà de la limite de la requête, elle n'est jamais examinée
par le matching. Ce script les fait rentrer dans le rang.

Usage :
    python -m scripts.backfill_quality_score              # batch=200 par défaut
    python -m scripts.backfill_quality_score --batch 50
    python -m scripts.backfill_quality_score --max 500    # arrêt après N offres

Idempotent : ne retouche pas les offres ayant déjà un quality_score.
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.scraped_offer import ScrapedOffer
from app.services.scraping.pipeline import process_offer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_quality_score")


def run(batch: int, max_offers: int | None) -> int:
    total = 0
    db = SessionLocal()
    try:
        while True:
            remaining = None if max_offers is None else max(0, max_offers - total)
            if remaining == 0:
                break
            limit = batch if remaining is None else min(batch, remaining)

            ids = db.execute(
                select(ScrapedOffer.id)
                .where(ScrapedOffer.quality_score.is_(None))
                .limit(limit)
            ).scalars().all()
            if not ids:
                break

            for offer_id in ids:
                process_offer(db, offer_id)
            db.commit()
            total += len(ids)
            logger.info("Batch traité : %d (cumul %d)", len(ids), total)
    finally:
        db.close()

    logger.info("Backfill terminé — %d offre(s) recalculée(s).", total)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=200, help="Taille de batch (défaut 200).")
    parser.add_argument("--max", type=int, default=None, help="Nombre max d'offres à traiter.")
    args = parser.parse_args()

    sys.exit(run(batch=args.batch, max_offers=args.max))


if __name__ == "__main__":
    main()
