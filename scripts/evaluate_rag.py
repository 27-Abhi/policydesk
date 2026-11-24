#!/usr/bin/env python3
"""PolicyDesk Offline RAG Evaluation Benchmark.

Calculates quantitative retrieval quality metrics without external LLM costs:
- Hit Rate @ 1 (Context@1)
- Hit Rate @ 3 (Recall@3)
- Mean Reciprocal Rank (MRR)
- Cosine Relevance Gate Accuracy (0.1500 threshold)
- Average Retrieval Cosine Scores (In-Domain vs Distractor)
"""

import json
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.graph.rag_nodes import get_active_store


def run_evaluation(benchmark_file: str = "evals/golden_benchmark.json", report_file: str = "evals/eval_report.json") -> dict:
    benchmark_path = PROJECT_ROOT / benchmark_file
    if not benchmark_path.exists():
        raise FileNotFoundError(f"Benchmark file not found at {benchmark_path}")

    with open(benchmark_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    store = get_active_store()

    total_cases = len(cases)
    relevant_cases = [c for c in cases if c.get("is_relevant")]
    oos_cases = [c for c in cases if not c.get("is_relevant")]

    hit_at_1 = 0
    hit_at_3 = 0
    reciprocal_ranks = []
    relevant_scores = []
    oos_scores = []

    true_positives = 0
    false_negatives = 0
    true_negatives = 0
    false_positives = 0

    per_query_results = []

    print("\n" + "=" * 80)
    print(" POLICYDESK RAG RETRIEVAL BENCHMARK EVALUATION")
    print("=" * 80)
    print(f"Total benchmark queries: {total_cases} ({len(relevant_cases)} In-Domain, {len(oos_cases)} Out-of-Scope)\n")

    for c in cases:
        qid = c["id"]
        query = c["query"]
        expected_pids = c.get("expected_parent_ids") or ([c["expected_parent_id"]] if c.get("expected_parent_id") else [])
        is_relevant = c.get("is_relevant", True)
        keywords = c.get("expected_keywords", [])

        results = store.retrieve(query, top_k=3, expand_window=1)
        top_score = results[0]["score"] if results else 0.0

        # Determine rank of expected document
        found_rank = None
        if is_relevant and expected_pids:
            for rank_idx, res in enumerate(results, 1):
                pids_in_result = [res.get("parent_id")] + res.get("expanded_pids", [])
                text_lower = res.get("text", "").lower()
                kw_match = any(kw.lower() in text_lower for kw in keywords) if keywords else False

                if any(p in pids_in_result for p in expected_pids) or (rank_idx == 1 and kw_match):
                    found_rank = rank_idx
                    break

        # Compute metric tallies
        if is_relevant:
            relevant_scores.append(top_score)
            if top_score >= 0.1500:
                true_positives += 1
            else:
                false_negatives += 1

            if found_rank == 1:
                hit_at_1 += 1
                hit_at_3 += 1
                reciprocal_ranks.append(1.0)
            elif found_rank in (2, 3):
                hit_at_3 += 1
                reciprocal_ranks.append(1.0 / found_rank)
            else:
                reciprocal_ranks.append(0.0)
        else:
            oos_scores.append(top_score)
            if top_score < 0.1500:
                true_negatives += 1
            else:
                false_positives += 1

        status_str = "PASS" if ((is_relevant and found_rank == 1) or (not is_relevant and top_score < 0.15)) else "FLAG"
        per_query_results.append({
            "id": qid,
            "query": query,
            "expected_parent_ids": expected_pids,
            "found_rank": found_rank,
            "top_score": round(top_score, 4),
            "is_relevant_expected": is_relevant,
            "is_relevant_gated": top_score >= 0.1500,
            "status": status_str,
        })

        top_id = results[0]["parent_id"] if results else "None"
        rank_display = f"Rank {found_rank}" if found_rank else "Miss"
        print(f"[{status_str:4}] {qid} | Score: {top_score:6.4f} | {rank_display:6} | Top: {top_id[:30]:30} | {query[:45]}")

    # Summary Calculations
    total_rel = len(relevant_cases)
    total_oos = len(oos_cases)

    hit_rate_1 = (hit_at_1 / total_rel) * 100 if total_rel else 0.0
    recall_at_3 = (hit_at_3 / total_rel) * 100 if total_rel else 0.0
    mrr = sum(reciprocal_ranks) / total_rel if total_rel else 0.0
    avg_rel_score = sum(relevant_scores) / total_rel if total_rel else 0.0
    avg_oos_score = sum(oos_scores) / total_oos if total_oos else 0.0

    tpr = (true_positives / total_rel) * 100 if total_rel else 0.0
    tnr = (true_negatives / total_oos) * 100 if total_oos else 0.0
    gating_accuracy = ((true_positives + true_negatives) / total_cases) * 100

    metrics = {
        "total_queries": total_cases,
        "in_domain_queries": total_rel,
        "out_of_scope_queries": total_oos,
        "hit_rate_at_1": round(hit_rate_1, 2),
        "hit_rate_at_3": round(recall_at_3, 2),
        "mean_reciprocal_rank": round(mrr, 4),
        "gating_sensitivity_tpr": round(tpr, 2),
        "gating_specificity_tnr": round(tnr, 2),
        "overall_gating_accuracy": round(gating_accuracy, 2),
        "avg_in_domain_cosine_score": round(avg_rel_score, 4),
        "avg_out_of_scope_cosine_score": round(avg_oos_score, 4),
        "relevance_threshold": 0.1500,
    }

    print("\n" + "=" * 80)
    print(" SUMMARY BENCHMARK SCORECARD")
    print("=" * 80)
    print(f" • Hit Rate @ 1 (Context@1)          : {metrics['hit_rate_at_1']}%  (Target: >= 90%)")
    print(f" • Hit Rate @ 3 (Recall@3)           : {metrics['hit_rate_at_3']}%  (Target: >= 95%)")
    print(f" • Mean Reciprocal Rank (MRR)        : {metrics['mean_reciprocal_rank']:.4f}  (1.0 = Perfect)")
    print(f" • Relevance Gating Accuracy         : {metrics['overall_gating_accuracy']}%  (Target: >= 95%)")
    print(f"   - Sensitivity (Relevant >= 0.15)  : {metrics['gating_sensitivity_tpr']}%")
    print(f"   - Specificity (Distractor < 0.15) : {metrics['gating_specificity_tnr']}%")
    print(f" • Avg In-Domain Cosine Similarity   : {metrics['avg_in_domain_cosine_score']:.4f}")
    print(f" • Avg Out-of-Scope Cosine Score     : {metrics['avg_out_of_scope_cosine_score']:.4f}")
    print("=" * 80 + "\n")

    report_path = Path(report_file)
    if not report_path.is_absolute():
        report_path = PROJECT_ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_data = {
        "metrics": metrics,
        "details": per_query_results,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    try:
        display_path = str(report_path.relative_to(PROJECT_ROOT))
    except ValueError:
        display_path = str(report_path)
    print(f"Evaluation report saved to: {display_path}\n")
    return metrics


if __name__ == "__main__":
    run_evaluation()
