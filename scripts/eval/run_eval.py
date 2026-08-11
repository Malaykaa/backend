"""Mesure la qualité du matching sur le jeu annoté.

Rejoue la recherche actuelle pour chaque intention du jeu, confronte l'ordre
obtenu aux annotations, et calcule precision@5, recall@10, MRR et nDCG@10.

Usage :
    python -m scripts.eval.run_eval
    python -m scripts.eval.run_eval --baseline resultats_avant.json
    python -m scripts.eval.run_eval --save resultats_apres.json
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.user_intent import UserIntent
from app.services.scraped_offer_service import ScrapedOfferService
from scripts.eval.metrics import (
    RELEVANT_THRESHOLD,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarize,
)

_DEFAULT_SET = os.path.join(os.path.dirname(__file__), "goldenset.json")


def _load(path: str) -> dict:
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _delta(current: float, baseline: float) -> str:
    diff = current - baseline
    if abs(diff) < 0.0005:
        return "     ="
    return f"{diff:+.3f}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goldenset", default=_DEFAULT_SET)
    parser.add_argument("--baseline", help="fichier de résultats à comparer")
    parser.add_argument("--save", help="où écrire les résultats")
    args = parser.parse_args()

    if not os.path.exists(args.goldenset):
        print(f"Jeu introuvable : {args.goldenset}")
        print("Le générer avec : python -m scripts.eval.build_goldenset")
        return 1

    data = _load(args.goldenset)
    cases = data.get("cases", [])

    annotated = [
        c for c in cases
        if any(o.get("relevance") is not None for o in c["candidates"])
    ]
    if not annotated:
        print("Aucune annotation trouvée — le jeu n'a pas encore été rempli.")
        print("Remplacer les \"relevance\": null par 0, 1 ou 2.")
        return 1
    if len(annotated) < len(cases):
        print(f"Attention : {len(cases) - len(annotated)} cas non annotés, ignorés.")

    db = SessionLocal()
    try:
        svc = ScrapedOfferService(db)
        per_case: list[dict] = []
        per_mode: dict[str, list[int]] = {}

        for case in annotated:
            truth = {
                o["offer_ref"]: o["relevance"]
                for o in case["candidates"]
                if o.get("relevance") is not None
            }
            total_relevant = sum(
                1 for r in truth.values() if r >= RELEVANT_THRESHOLD
            )

            intent = db.get(UserIntent, case["intent"]["id"])
            if intent is None:
                continue  # intention supprimée depuis la génération du jeu

            results = await svc.search_for_matching(intent, limit=10)
            # Une offre remontée mais absente du jeu n'est pas jugeable : on la
            # neutralise plutôt que de la compter comme mauvaise, sinon toute
            # amélioration du rappel dégraderait mécaniquement le score.
            relevances = [
                truth.get(o.get("offer_ref"), 0) for o in results
            ]
            for offer in results:
                mode = offer.get("match_mode", "?")
                per_mode.setdefault(mode, []).append(
                    truth.get(offer.get("offer_ref"), 0)
                )

            per_case.append({
                "intent_id": case["intent"]["id"],
                "summary": (case["intent"].get("summary") or "")[:60],
                "precision@5": precision_at_k(relevances, 5),
                "recall@10": recall_at_k(relevances, 10, total_relevant),
                "mrr": reciprocal_rank(relevances),
                "ndcg@10": ndcg_at_k(relevances, 10),
            })

        if not per_case:
            print("Aucun cas exploitable (intentions supprimées ?).")
            return 1

        overall = summarize(per_case)
        baseline = _load(args.baseline)["overall"] if args.baseline else None

        print()
        print(f"Cas évalués : {len(per_case)}")
        print()
        header = f"{'Métrique':<14}{'Valeur':>9}"
        if baseline:
            header += f"{'Référence':>11}{'Écart':>9}"
        print(header)
        print("-" * len(header))
        for key in ("precision@5", "recall@10", "mrr", "ndcg@10"):
            line = f"{key:<14}{_fmt(overall[key]):>9}"
            if baseline:
                line += f"{_fmt(baseline[key]):>11}{_delta(overall[key], baseline[key]):>9}"
            print(line)

        if per_mode:
            print()
            print(f"{'Mode':<12}{'offres':>8}{'part utile':>12}")
            print("-" * 32)
            for mode, rels in sorted(per_mode.items()):
                useful = sum(1 for r in rels if r >= RELEVANT_THRESHOLD) / len(rels)
                print(f"{mode:<12}{len(rels):>8}{useful:>12.3f}")

        worst = sorted(per_case, key=lambda c: c["precision@5"])[:3]
        print()
        print("Cas les plus faibles (à inspecter en priorité) :")
        for case in worst:
            print(f"  precision@5={_fmt(case['precision@5'])}  {case['summary']}")

        if args.save:
            with io.open(args.save, "w", encoding="utf-8", newline="\n") as f:
                json.dump(
                    {
                        "measured_at": datetime.now(timezone.utc).isoformat(),
                        "cases_count": len(per_case),
                        "overall": overall,
                        "per_case": per_case,
                    },
                    f, ensure_ascii=False, indent=2,
                )
                f.write("\n")
            print()
            print(f"Résultats écrits dans {args.save}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
