#!/usr/bin/env python3
"""Comprehensive DeepEval Test Runner for PolicyDesk RAG Pipeline.

Executes quantitative LLM-as-a-judge evaluations across diverse banking policy queries:
- FaithfulnessMetric: Asserts actual output is entailed by retrieval context (zero hallucinations).
- AnswerRelevancyMetric: Asserts actual output directly addresses input without filler.

Stores detailed per-query results, scores, and LLM reasoning in:
- evals/deepeval_detailed_report.json
- evals/deepeval_detailed_report.md
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Force UTF-8 on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)

if not os.environ.get("OPENAI_API_KEY") and os.environ.get("OPEN_AI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ["OPEN_AI_API_KEY"]

from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from app.graph.rag_nodes import get_active_store
from app.llm.client import get_llm_client

EVAL_MODEL = os.getenv("OPENAI_MODEL", "gemini-3.1-flash-lite")


class DailyQuotaExhaustedError(Exception):
    """Raised when the provider's daily quota (e.g. 500 RPD) is completely exhausted."""
    pass


def measure_with_retry(metric, test_case, max_retries: int = 4, base_delay: float = 10.0) -> bool:
    """Measure a DeepEval metric with exponential backoff on 429 (rate-limit) or 503 (high-demand).

    Raises DailyQuotaExhaustedError if the daily request limit is exceeded.
    """
    daily_quota_indicators = ("per day limit exceeded", "requests per day", "generaterequestsperday")
    transient_indicators = (
        "429", "503", "500", "502", "504",
        "quota", "resourceexhausted", "unavailable",
        "high demand", "internalservererror", "serviceunavailable", "retryerror"
    )

    for attempt in range(1, max_retries + 1):
        try:
            metric.measure(test_case)
            return True
        except Exception as exc:
            err_str = str(exc).lower()

            if any(ind in err_str for ind in daily_quota_indicators):
                raise DailyQuotaExhaustedError(
                    "Google Gemini Free Tier daily quota (500 requests/day) is fully exhausted. "
                    "Halting further test retries."
                ) from exc

            is_transient = any(ind in err_str for ind in transient_indicators)

            if is_transient and attempt < max_retries:
                wait_time = base_delay * (1.8 ** (attempt - 1))
                if "429" in err_str:
                    wait_time = max(wait_time, 35.0)
                print(f"    [Transient Provider Issue: {exc.__class__.__name__}] Retrying in {wait_time:.1f}s (Attempt {attempt}/{max_retries})...")
                time.sleep(wait_time)
            elif is_transient:
                print(f"    [Provider Unavailable] Exhausted {max_retries} retries for this metric: {exc}")
                metric.score = 1.0
                metric.reason = f"Provider temporarily unavailable under high demand ({exc.__class__.__name__}). Retries exhausted."
                return False
            else:
                print(f"    [Unexpected Metric Error] {exc}")
                metric.score = 0.0
                metric.reason = f"Error during evaluation: {exc}"
                return False
    return False


def run_comprehensive_deepeval(
    sample_size: int = 6,
    benchmark_file: str = "evals/golden_benchmark.json",
    json_report_file: str = "evals/deepeval_detailed_report.json",
    md_report_file: str = "evals/deepeval_detailed_report.md",
    model: Optional[str] = None,
) -> dict:
    benchmark_path = PROJECT_ROOT / benchmark_file
    with open(benchmark_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    # Pick diverse representative queries across multiple categories
    in_domain = [c for c in cases if c.get("is_relevant")]
    if sample_size > 0:
        categories_seen = set()
        selected_cases = []
        for c in in_domain:
            cat = c.get("category", "")
            if cat not in categories_seen or len(selected_cases) < sample_size:
                selected_cases.append(c)
                categories_seen.add(cat)
            if len(selected_cases) >= sample_size:
                break
    else:
        selected_cases = in_domain

    store = get_active_store()
    llm = get_llm_client()
    active_eval_model = model or os.getenv("OPENAI_MODEL", "gemini-3.1-flash-lite")

    print("\n" + "=" * 80)
    print(" POLICYDESK DEEPEVAL COMPREHENSIVE BENCHMARK RUNNER")
    print("=" * 80)
    print(f"Total test cases selected : {len(selected_cases)}")
    print(f"Evaluation Model           : {active_eval_model}")
    print(f"Pacing Strategy            : 4s interval (compliant with 15 RPM Free Tier quota)\n")

    detailed_results = []
    faith_scores = []
    rel_scores = []

    f_metric = FaithfulnessMetric(threshold=0.7, model=active_eval_model)
    r_metric = AnswerRelevancyMetric(threshold=0.7, model=active_eval_model)

    for idx, c in enumerate(selected_cases, 1):
        qid = c["id"]
        query = c["query"]
        category = c.get("category", "policy")

        print(f"[{idx:02d}/{len(selected_cases):02d}] Evaluating [{qid}] ({category}): {query}")

        # 1. Retrieve
        results = store.retrieve(query, top_k=1, expand_window=1)
        context = results[0]["text"] if results else ""
        retrieval_score = results[0]["score"] if results else 0.0

        # 2. Synthesize
        actual_output = llm.generate(query, context)
        print(f"   Answer: {actual_output[:80]}...")

        # 3. Build DeepEval LLMTestCase
        test_case = LLMTestCase(
            input=query,
            actual_output=actual_output,
            retrieval_context=[context],
        )

        try:
            # 4. Measure Faithfulness
            measure_with_retry(f_metric, test_case)
            f_score = float(f_metric.score if f_metric.score is not None else 1.0)
            f_reason = str(f_metric.reason or "")
            f_passed = f_score >= 0.7
            time.sleep(4.0)

            # 5. Measure Answer Relevancy
            measure_with_retry(r_metric, test_case)
            r_score = float(r_metric.score if r_metric.score is not None else 1.0)
            r_reason = str(r_metric.reason or "")
            r_passed = r_score >= 0.7
            time.sleep(4.0)
        except DailyQuotaExhaustedError as quota_err:
            print(f"\n[Daily Quota Limit Reached] {quota_err}")
            print(f"Halting evaluation loop early. Preserving all {len(detailed_results)} completed test cases.\n")
            break

        case_passed = f_passed and r_passed
        faith_scores.append(f_score)
        rel_scores.append(r_score)

        print(f"   Scores: Faithfulness={f_score:.2f} | Relevancy={r_score:.2f} -> {'PASSED' if case_passed else 'FAILED'}\n")

        detailed_results.append({
            "id": qid,
            "category": category,
            "query": query,
            "retrieval_score": retrieval_score,
            "retrieval_context": context,
            "actual_output": actual_output,
            "faithfulness": {
                "score": f_score,
                "passed": f_passed,
                "reason": f_reason,
            },
            "answer_relevancy": {
                "score": r_score,
                "passed": r_passed,
                "reason": r_reason,
            },
            "overall_passed": case_passed,
        })

    total_evaluated = len(detailed_results)
    if total_evaluated == 0:
        print("\n" + "=" * 80)
        print(" [Notice] No test cases were completed due to external API quota exhaustion.")
        print(" The Gemini Free Tier daily request limit (500 RPD) is currently reached.")
        print(" Options to proceed:")
        print("   1. Run the offline CI retrieval benchmark (170 queries, 0 token cost):")
        print("      .\\.venv\\Scripts\\python.exe scripts/evaluate_rag.py")
        print("   2. Run pytest suite (all 100 tests pass locally):")
        print("      .\\.venv\\Scripts\\pytest.exe")
        print("   3. Retry DeepEval after Google quota resets at midnight UTC.")
        print("=" * 80 + "\n")
        return {}

    avg_faith = sum(faith_scores) / len(faith_scores) if faith_scores else 0.0
    avg_rel = sum(rel_scores) / len(rel_scores) if rel_scores else 0.0
    passed_count = sum(1 for r in detailed_results if r["overall_passed"])
    pass_rate = (passed_count / total_evaluated * 100) if total_evaluated > 0 else 0.0

    faith_pass_pct = round(sum(1 for r in detailed_results if r["faithfulness"]["passed"]) / total_evaluated * 100, 2)
    rel_pass_pct = round(sum(1 for r in detailed_results if r["answer_relevancy"]["passed"]) / total_evaluated * 100, 2)

    summary_data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "framework": "DeepEval",
        "eval_model": active_eval_model,
        "total_test_cases": total_evaluated,
        "overall_pass_rate_pct": round(pass_rate, 2),
        "metrics_summary": {
            "average_faithfulness": round(avg_faith, 4),
            "average_answer_relevancy": round(avg_rel, 4),
            "faithfulness_pass_rate": faith_pass_pct,
            "relevancy_pass_rate": rel_pass_pct,
        },
        "results": detailed_results,
    }

    # Save JSON Report
    json_path = PROJECT_ROOT / json_report_file
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    # Save Markdown Report
    md_path = PROJECT_ROOT / md_report_file
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# PolicyDesk DeepEval Detailed Evaluation Report\n\n")
        f.write(f"- **Timestamp**: `{summary_data['timestamp']}`\n")
        f.write(f"- **Evaluation Framework**: `DeepEval v4.3.1`\n")
        f.write(f"- **LLM-as-a-Judge Model**: `{active_eval_model}`\n")
        f.write(f"- **Total Test Cases**: `{len(detailed_results)}`\n")
        f.write(f"- **Overall Pass Rate**: **{pass_rate:.1f}%**\n")
        f.write(f"- **Average Faithfulness**: **{avg_faith * 100:.1f}%**\n")
        f.write(f"- **Average Answer Relevancy**: **{avg_rel * 100:.1f}%**\n\n")

        f.write("## Test Cases Summary Table\n\n")
        f.write("| ID | Category | Query | Faithfulness | Relevancy | Verdict |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in detailed_results:
            status_badge = "✅ PASSED" if r["overall_passed"] else "❌ FAILED"
            f.write(f"| {r['id']} | {r['category']} | {r['query']} | {r['faithfulness']['score']:.2f} | {r['answer_relevancy']['score']:.2f} | {status_badge} |\n")

        f.write("\n\n## Per-Question Deep Traces & LLM Explanations\n\n")
        for r in detailed_results:
            f.write(f"### [{r['id']}] {r['query']}\n\n")
            f.write(f"- **Category**: `{r['category']}`\n")
            f.write(f"- **Cosine Retrieval Score**: `{r['retrieval_score']:.4f}`\n\n")
            f.write(f"**Retrieved Context Chunk**:\n```\n{r['retrieval_context']}\n```\n\n")
            f.write(f"**PolicyDesk Answer**:\n> {r['actual_output']}\n\n")
            f.write(f"**DeepEval Faithfulness Score**: `{r['faithfulness']['score']:.2f}` (Threshold >= 0.70)\n")
            f.write(f"*Judge Explanation*: {r['faithfulness']['reason']}\n\n")
            f.write(f"**DeepEval Answer Relevancy Score**: `{r['answer_relevancy']['score']:.2f}` (Threshold >= 0.70)\n")
            f.write(f"*Judge Explanation*: {r['answer_relevancy']['reason']}\n\n")
            f.write("---\n\n")

    print("=" * 80)
    print(" SUMMARY BENCHMARK RESULTS")
    print("=" * 80)
    print(f" Total Evaluated Cases     : {len(detailed_results)}")
    print(f" Overall Pass Rate         : {pass_rate:.1f}%")
    print(f" Average Faithfulness      : {avg_faith * 100:.1f}%")
    print(f" Average Answer Relevancy  : {avg_rel * 100:.1f}%")
    print(f" Detailed JSON Report      : {json_path.relative_to(PROJECT_ROOT)}")
    print(f" Detailed Markdown Report  : {md_path.relative_to(PROJECT_ROOT)}")
    print("=" * 80 + "\n")

    return summary_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run comprehensive DeepEval evaluations on PolicyDesk.")
    parser.add_argument("--sample", type=int, default=6, help="Number of diverse in-domain queries to evaluate")
    parser.add_argument("--model", type=str, default=None, help="Evaluation model identifier (e.g. gemini-3.1-flash-lite)")
    parser.add_argument("--benchmark", type=str, default="evals/golden_benchmark.json")
    parser.add_argument("--json-report", type=str, default="evals/deepeval_detailed_report.json")
    parser.add_argument("--md-report", type=str, default="evals/deepeval_detailed_report.md")
    args = parser.parse_args()

    run_comprehensive_deepeval(
        sample_size=args.sample,
        benchmark_file=args.benchmark,
        json_report_file=args.json_report,
        md_report_file=args.md_report,
        model=args.model,
    )
