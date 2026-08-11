"""Construit le squelette du jeu d'évaluation à partir des données réelles.

Tire des intentions en base, exécute la recherche actuelle, et écrit un
`goldenset.json` où chaque offre candidate attend une annotation manuelle
(`null` → 0, 1 ou 2). Voir scripts/eval/README.md.

Le script n'annote rien : un jeu de référence généré automatiquement mesurerait
la recherche par elle-même, ce qui ne prouverait rien.

Usage :
    python -m scripts.eval.build_goldenset
    python -m scripts.eval.build_goldenset --intents 40 --depth 15 --out mon.json
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.user_intent import UserIntent
from app.services.scraped_offer_service import ScrapedOfferService

_DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "goldenset.json")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intents", type=int, default=40)
    parser.add_argument("--depth", type=int, default=15)
    parser.add_argument("--out", default=_DEFAULT_OUT)
    args = parser.parse_args()

    if os.path.exists(args.out):
        print(f"{args.out} existe déjà — annotations en danger.")
        print("Renommer ou supprimer le fichier avant de régénérer.")
        return 1

    db = SessionLocal()
    try:
        intents = list(
            db.execute(
                select(UserIntent)
                .where(UserIntent.intent_summary.is_not(None))
                .order_by(func.random())
                .limit(args.intents)
            ).scalars().all()
        )
        if not intents:
            print("Aucune UserIntent exploitable en base.")
            return 1

        svc = ScrapedOfferService(db)
        cases: list[dict] = []

        for intent in intents:
            offers = await svc.search_for_matching(intent, limit=args.depth)
            if not offers:
                continue
            cases.append({
                "intent": {
                    "id": str(intent.id),
                    "summary": intent.intent_summary,
                    "type": intent.intent_type,
                    "domain": intent.domain,
                    "level": intent.level,
                    "location": intent.location,
                    "keywords": list(intent.keywords or []),
                },
                "candidates": [
                    {
                        "offer_ref": o.get("offer_ref"),
                        "title": o.get("title"),
                        "type": o.get("type"),
                        "location": o.get("location"),
                        "match_score": o.get("match_score"),
                        "match_mode": o.get("match_mode"),
                        # ── À REMPLIR À LA MAIN : 0, 1 ou 2 ──
                        "relevance": None,
                    }
                    for o in offers
                ],
            })

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "depth": args.depth,
            "scale": {"0": "hors sujet", "1": "acceptable", "2": "pertinente"},
            "cases": cases,
        }
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")

        total = sum(len(c["candidates"]) for c in cases)
        print(f"{len(cases)} intention(s), {total} offre(s) à annoter.")
        print(f"Écrit dans {args.out}")
        print()
        print("Prochaine étape : remplacer chaque \"relevance\": null par 0, 1 ou 2.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
