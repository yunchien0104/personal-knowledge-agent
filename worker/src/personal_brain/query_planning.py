from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from personal_brain.text_normalization import normalize_for_retrieval, query_keywords


_QUESTION_PREFIXES = re.compile(
    r"^(?:請問|請|幫我|可以|能否|what|why|how|when|where|which|who|compare|summarize)\s*",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(r"(?:[?？;；]|\s+(?:and|versus|vs\.?|compared with)\s+|以及|與|和|及|並且)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    normalized_question: str
    intent: str
    lexical_queries: tuple[str, ...]
    semantic_queries: tuple[str, ...]
    keywords: tuple[str, ...]


def _intent(question: str) -> str:
    lowered = question.casefold()
    if any(token in lowered for token in ("比較", "差異", " versus ", " vs ", "compare")):
        return "compare"
    if any(token in lowered for token in ("原因", "為什麼", "影響", "導致", "why", "cause", "impact")):
        return "explain"
    if any(token in lowered for token in ("趨勢", "變化", "歷年", "trend", "over time")):
        return "trend"
    if any(token in lowered for token in ("摘要", "重點", "總結", "summary", "summarize")):
        return "summarize"
    return "fact_find"


def build_query_plan(question: str, *, max_queries: int = 3) -> PlannedQuery:
    normalized = normalize_for_retrieval(" ".join(question.split()))[:500]
    if len(normalized) < 2:
        raise ValueError("question is too short after normalization")

    clauses = [part.strip(" ,，。.!！") for part in _CLAUSE_SPLIT.split(normalized)]
    clauses = [part for part in clauses if len(part) >= 2]
    stripped = _QUESTION_PREFIXES.sub("", normalized).strip(" ,，。.?？!！")
    keywords = query_keywords(normalized)
    keyword_query = " ".join(keywords[:12])

    candidates: list[str] = [normalized]
    if keyword_query:
        candidates.append(keyword_query)
    candidates.extend(clauses)
    if stripped and stripped != normalized:
        candidates.append(stripped)

    lexical: list[str] = []
    for candidate in candidates:
        candidate = candidate[:500]
        if candidate not in lexical:
            lexical.append(candidate)
        if len(lexical) == max_queries:
            break

    semantic = [normalized]
    if stripped and stripped != normalized:
        semantic.append(stripped)
    for clause in clauses:
        if clause not in semantic:
            semantic.append(clause)
        if len(semantic) == max_queries:
            break

    return PlannedQuery(
        normalized_question=normalized,
        intent=_intent(normalized),
        lexical_queries=tuple(lexical),
        semantic_queries=tuple(semantic[:max_queries]),
        keywords=tuple(keywords),
    )


class ProductionQueryPlanner:
    """Deterministic multilingual planner and safe fallback for internal research."""

    provider = "local"
    model_name = "production-query-planner-v1"

    def plan(self, question: str) -> Mapping[str, Any]:
        planned = build_query_plan(question)
        subquestions = list(planned.semantic_queries)
        return {
            "question": question,
            "normalizedQuestion": planned.normalized_question,
            "intent": planned.intent,
            "subquestions": subquestions,
            "lexicalQueries": list(planned.lexical_queries),
            "semanticQueries": list(planned.semantic_queries),
            "searchKeywords": list(planned.keywords),
            "scope": "Authenticated active Personal Brain revisions only",
            "evidenceRequirements": [
                "Every factual claim must cite active original Evidence with a verbatim quote.",
                "Report insufficient evidence instead of filling gaps from model knowledge.",
            ],
            "maxCostUsd": 0,
            "maxQueries": len(subquestions),
            "maxRounds": 1,
            "stopConditions": [
                "Stop after bounded hybrid retrieval and one repair attempt.",
                "Do not publish unless every atomic claim passes verification.",
            ],
        }
