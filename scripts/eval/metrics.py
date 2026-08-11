"""Métriques de qualité du classement — sans dépendance à la base.

Isolées ici pour être testables unitairement : une métrique fausse invaliderait
silencieusement toutes les décisions prises à partir d'elle.

Convention d'annotation : 0 hors sujet, 1 acceptable, 2 pertinente.
Une offre est comptée « pertinente » à partir de 1 pour precision et recall —
une offre acceptable reste utile à l'utilisateur, elle n'est pas une erreur.
"""

from __future__ import annotations

import math

RELEVANT_THRESHOLD = 1


def precision_at_k(relevances: list[int], k: int) -> float:
    """Part d'offres utiles parmi les k premières.

    La métrique la plus proche du vécu : au-delà des premiers résultats,
    personne ne lit.
    """
    top = relevances[:k]
    if not top:
        return 0.0
    hits = sum(1 for r in top if r >= RELEVANT_THRESHOLD)
    return hits / len(top)


def recall_at_k(relevances: list[int], k: int, total_relevant: int) -> float:
    """Part des offres utiles du corpus annoté effectivement remontées dans les k.

    `total_relevant` doit compter toutes les offres utiles connues pour cette
    intention, pas seulement celles présentes dans la liste évaluée.
    """
    if total_relevant <= 0:
        return 0.0
    hits = sum(1 for r in relevances[:k] if r >= RELEVANT_THRESHOLD)
    return min(1.0, hits / total_relevant)


def reciprocal_rank(relevances: list[int]) -> float:
    """1 / rang de la première offre utile. 0 si aucune.

    Répond à « combien de déchet avant la première chose valable ».
    """
    for rank, rel in enumerate(relevances, start=1):
        if rel >= RELEVANT_THRESHOLD:
            return 1.0 / rank
    return 0.0


def dcg_at_k(relevances: list[int], k: int) -> float:
    """Gain cumulé actualisé — récompense les bonnes offres bien classées."""
    return sum(
        (2 ** rel - 1) / math.log2(rank + 1)
        for rank, rel in enumerate(relevances[:k], start=1)
    )


def ndcg_at_k(relevances: list[int], k: int) -> float:
    """DCG normalisé par le classement idéal, dans [0, 1].

    Seule métrique ici à exploiter les trois niveaux d'annotation : elle
    distingue « pertinente en tête » de « acceptable en tête », ce que
    precision@k confond.
    """
    ideal = dcg_at_k(sorted(relevances, reverse=True), k)
    if ideal == 0.0:
        return 0.0
    return dcg_at_k(relevances, k) / ideal


def summarize(per_case: list[dict]) -> dict[str, float]:
    """Moyenne les métriques sur l'ensemble des cas.

    Moyenne par cas et non par offre : chaque intention pèse pareil, sinon
    une intention à quinze candidats écraserait une intention à trois.
    """
    if not per_case:
        return {}
    keys = ("precision@5", "recall@10", "mrr", "ndcg@10")
    return {k: sum(c[k] for c in per_case) / len(per_case) for k in keys}
