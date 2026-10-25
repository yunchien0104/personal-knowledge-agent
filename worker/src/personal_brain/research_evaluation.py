from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall_at_5: float
    recall_at_10: float
    precision_at_5: float
    mrr: float
    ndcg_at_10: float
    evidence_coverage: float


def evaluate_rankings(
    expected: Mapping[str, set[str]],
    rankings: Mapping[str, Sequence[str]],
) -> RetrievalMetrics:
    if not expected:
        raise ValueError("expected judgments must not be empty")
    recall_5: list[float] = []
    recall_10: list[float] = []
    precision_5: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    found: set[tuple[str, str]] = set()
    all_relevant = {(query_id, evidence_id) for query_id, ids in expected.items() for evidence_id in ids}
    for query_id, relevant in expected.items():
        if not relevant:
            raise ValueError("each query needs at least one relevant Evidence judgment")
        ranked = list(dict.fromkeys(rankings.get(query_id, ())))
        top_5 = ranked[:5]
        top_10 = ranked[:10]
        recall_5.append(len(set(top_5) & relevant) / len(relevant))
        recall_10.append(len(set(top_10) & relevant) / len(relevant))
        precision_5.append(len(set(top_5) & relevant) / 5)
        first = next((index for index, item in enumerate(ranked, start=1) if item in relevant), None)
        reciprocal_ranks.append(0.0 if first is None else 1.0 / first)
        dcg = sum(1.0 / math.log2(index + 1) for index, item in enumerate(top_10, start=1) if item in relevant)
        ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(relevant), 10) + 1))
        ndcgs.append(dcg / ideal)
        found.update((query_id, item) for item in top_10 if item in relevant)
    mean = lambda values: sum(values) / len(values)
    return RetrievalMetrics(
        mean(recall_5), mean(recall_10), mean(precision_5), mean(reciprocal_ranks),
        mean(ndcgs), len(found) / len(all_relevant),
    )
