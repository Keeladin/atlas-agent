from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from .models import FusionResult, RankedCandidate


def reciprocal_rank_fusion(rankings: Sequence[Sequence[RankedCandidate]], *, k: int = 60,
                           weights: dict[str, float] | None = None) -> list[FusionResult]:
    """Fuse incomparable rankings by rank only; deterministic ties use item id."""
    scores: dict[str, float] = defaultdict(float)
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    for ranking in rankings:
        for candidate in ranking:
            weight = 1.0 if weights is None else float(weights.get(candidate.source, 1.0))
            scores[candidate.item_id] += weight / (k + candidate.rank)
            ranks[candidate.item_id][candidate.source] = candidate.rank
    return sorted(
        (FusionResult(item_id=item_id, score=score, ranks=ranks[item_id]) for item_id, score in scores.items()),
        key=lambda row: (-row.score, row.item_id),
    )
