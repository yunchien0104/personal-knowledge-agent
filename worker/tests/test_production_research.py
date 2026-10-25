from __future__ import annotations

import json
from pathlib import Path

from personal_brain.query_planning import ProductionQueryPlanner, build_query_plan
from personal_brain.research_evaluation import evaluate_rankings
from personal_brain.text_normalization import normalize_for_retrieval, query_keywords


def test_cjk_per_character_linebreak_is_repaired_only_for_retrieval() -> None:
    original = "台\n灣\n科\n技產業"
    assert normalize_for_retrieval(original) == "台灣科技產業"
    assert original == "台\n灣\n科\n技產業"
    assert "台灣" in query_keywords(original)


def test_planner_is_multilingual_bounded_and_not_topic_hardcoded() -> None:
    for question in (
        "台灣科技產業的主要成長動能是什麼？",
        "What drove subscription revenue growth?",
        "AI server 的 demand 與 revenue 有何關係？",
    ):
        plan = ProductionQueryPlanner().plan(question)
        assert 1 <= plan["maxQueries"] <= 3
        assert len(plan["semanticQueries"]) <= 3
        assert len(plan["lexicalQueries"]) <= 3
        assert plan["question"] == question


def test_query_rewrite_splits_comparison_without_identity_fields() -> None:
    plan = build_query_plan("Compare product A and product B revenue trends")
    assert plan.intent == "compare"
    assert len(plan.semantic_queries) >= 2
    assert not hasattr(plan, "user_id")


def test_golden_dataset_has_manual_judgments_and_metric_thresholds() -> None:
    path = Path(__file__).parent / "research" / "eval_data" / "retrieval_golden_v1.json"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    expected = {case["id"]: set(case["relevantEvidenceIds"]) for case in dataset["cases"]}
    perfect = {case["id"]: case["relevantEvidenceIds"] for case in dataset["cases"]}
    metrics = evaluate_rankings(expected, perfect)
    assert metrics.recall_at_5 == 1
    assert metrics.recall_at_10 == 1
    assert metrics.mrr == 1
    assert metrics.ndcg_at_10 == 1
    assert metrics.evidence_coverage == 1


def test_migration_binds_retrieval_to_job_and_indexes_both_paths() -> None:
    migration = (
        Path(__file__).parents[2]
        / "supabase" / "migrations" / "20261024185133_production_research_pipeline.sql"
    ).read_text(encoding="utf-8")
    assert "hybrid_retrieve_for_research_job" in migration
    assert "lease_generation = p_lease_generation" in migration
    assert "research scope does not match trusted Job scope" in migration
    assert "using gin (search_vector)" in migration
    assert "using hnsw (embedding extensions.vector_cosine_ops)" in migration
    assert "research_citations_require_retrieval" in migration
    assert "revoke execute on function public.hybrid_retrieve_for_worker" in migration
