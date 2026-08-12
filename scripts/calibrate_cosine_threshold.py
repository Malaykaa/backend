"""Calibre _MIN_COSINE_SIM sur la distribution réelle des similarités.

Le seuil actuel (0.35) a été posé a priori, à partir de deux mesures ponctuelles.
Deux raisons de le remesurer :

- les vecteurs Perplexity sont quantifiés en int8 puis divisés par 128, ce qui
  resserre la distribution des similarités par rapport à des float32 ;
- un seuil trop haut vide la voie sémantique, un seuil trop bas y laisse entrer
  du bruit — et dans les deux cas la fusion hybride se dégrade silencieusement.

Méthode : on tire au sort des intentions réelles, on interroge pgvector sans
aucun filtre de seuil, et on observe où se situent les similarités des offres
retournées. Le seuil proposé est le percentile sous lequel on considère que le
signal n'est plus exploitable.

Usage :
    python -m scripts.calibrate_cosine_threshold
    python -m scripts.calibrate_cosine_threshold --intents 80 --depth 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.scraped_offer import ScrapedOffer
from app.models.user_intent import UserIntent
from app.services.embedding_service import get_embedding_service
from app.services.scraped_offer_service import (
    _MIN_COSINE_SIM,
    _build_query_text,
)

logging.basicConfig(level=logging.WARNING, format="%(message)s")


def _percentile(sorted_values: list[float], p: float) -> float:
    """Percentile par interpolation linéaire. `sorted_values` doit être trié."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * p
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    weight = pos - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--intents", type=int, default=50,
        help="nombre d'intentions échantillonnées (défaut : 50)",
    )
    parser.add_argument(
        "--depth", type=int, default=20,
        help="offres examinées par intention (défaut : 20)",
    )
    args = parser.parse_args()

    svc = get_embedding_service()
    if not svc.available:
        print("PERPLEXITY_API_KEY absente — calibration impossible.")
        print("La voie sémantique est inactive : rien à calibrer pour l'instant.")
        return 1

    db = SessionLocal()
    try:
        indexed = db.execute(
            select(func.count(ScrapedOffer.id)).where(
                ScrapedOffer.embedding.is_not(None),
                ScrapedOffer.is_active.is_(True),
            )
        ).scalar_one()
        total = db.execute(
            select(func.count(ScrapedOffer.id)).where(ScrapedOffer.is_active.is_(True))
        ).scalar_one()

        coverage = (indexed / total * 100) if total else 0.0
        print(f"Corpus actif      : {total} offres")
        print(f"Indexées          : {indexed} ({coverage:.1f} %)")

        if indexed < 200:
            print()
            print("Moins de 200 offres indexées : l'échantillon serait trop maigre")
            print("pour un percentile fiable. Relancer une fois l'index rempli.")
            return 1

        intents = list(
            db.execute(
                select(UserIntent)
                .order_by(func.random())
                .limit(args.intents)
            ).scalars().all()
        )
        if not intents:
            print("Aucune UserIntent en base — rien à échantillonner.")
            return 1

        print(f"Intentions tirées : {len(intents)}")
        print()

        # Similarité du meilleur résultat de chaque intention : c'est elle qui
        # décide si la voie sémantique a quelque chose à dire.
        best_per_intent: list[float] = []
        # Toutes les similarités observées, pour situer le bruit de fond.
        all_sims: list[float] = []

        for intent in intents:
            query_text = _build_query_text(intent)
            if not query_text:
                continue
            vec = await svc.embed(query_text)
            if vec is None:
                continue

            distance = ScrapedOffer.embedding.cosine_distance(vec).label("d")
            rows = db.execute(
                select(distance)
                .where(
                    ScrapedOffer.embedding.is_not(None),
                    ScrapedOffer.is_active.is_(True),
                )
                .order_by(distance)
                .limit(args.depth)
            ).all()
            sims = [max(0.0, 1.0 - float(r[0])) for r in rows]
            if not sims:
                continue
            best_per_intent.append(sims[0])
            all_sims.extend(sims)

        if not best_per_intent:
            print("Aucune similarité mesurable (échecs d'embedding ?).")
            return 1

        best_per_intent.sort()
        all_sims.sort()

        print(f"{'':<22}{'p05':>8}{'p25':>8}{'médiane':>10}{'p75':>8}{'p95':>8}")
        print("-" * 64)
        for label, data in (
            ("Meilleur par intention", best_per_intent),
            ("Toutes similarités", all_sims),
        ):
            print(
                f"{label:<22}"
                f"{_percentile(data, 0.05):>8.3f}"
                f"{_percentile(data, 0.25):>8.3f}"
                f"{_percentile(data, 0.50):>10.3f}"
                f"{_percentile(data, 0.75):>8.3f}"
                f"{_percentile(data, 0.95):>8.3f}"
            )

        # Un seuil utile laisse passer la quasi-totalité des meilleurs résultats
        # (sinon la voie sémantique se tait) tout en restant au-dessus du bruit
        # de fond mesuré sur l'ensemble des candidats.
        floor = _percentile(best_per_intent, 0.05)
        noise = _percentile(all_sims, 0.50)
        suggested = round(max(0.05, min(floor, noise)) - 0.02, 2)

        muted = sum(1 for s in best_per_intent if s < _MIN_COSINE_SIM)
        muted_new = sum(1 for s in best_per_intent if s < suggested)

        print()
        print(f"Seuil actuel   : {_MIN_COSINE_SIM:.2f}  → {muted}/{len(best_per_intent)} "
              f"intention(s) sans aucun résultat sémantique")
        print(f"Seuil suggéré  : {suggested:.2f}  → {muted_new}/{len(best_per_intent)} "
              f"intention(s) sans résultat")
        print()
        if muted > len(best_per_intent) * 0.2:
            print("Le seuil actuel prive plus d'une intention sur cinq de la voie")
            print("sémantique — l'abaisser élargirait utilement la fusion.")
        elif suggested > _MIN_COSINE_SIM + 0.05:
            print("Le seuil actuel laisse passer des candidats proches du bruit de")
            print("fond — le relever améliorerait la précision.")
        else:
            print("Le seuil actuel est cohérent avec la distribution observée.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
