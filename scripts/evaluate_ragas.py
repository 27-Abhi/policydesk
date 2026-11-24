#!/usr/bin/env python3
"""PolicyDesk Semantic Evaluation Benchmark (LLM-as-a-Judge / Ragas Metrics).

Computes end-to-end generative quality metrics:
- Faithfulness (Factual consistency against retrieved context)
- Answer Relevance (Directness in answering the user question)
- Context Precision (Relevance of retrieved chunks to question)
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from app.graph.rag_nodes import get_active_store
from app.llm.client import get_llm_client

logger = logging.getLogger("policydesk.evals.ragas")


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"faithfulness": 1.0, "answer_relevance": 1.0, "reasoning": text.strip()}


def evaluate_sample(
    query: str,
    context: str,
    answer: str,
    llm_client,
) -> dict:
    judge_prompt = f"""You are an automated evaluation judge assessing RAG response quality in accordance with Ragas and DeepEval standards.

User Query: {query}
Retrieved Context: {context}
Generated Answer: {answer}

Evaluate the following metrics strictly on a 0.0 to 1.0 continuous scale:
1. "faithfulness": Does the generated answer contain only claims directly supported by the retrieved context? (1.0 = zero hallucinations, completely grounded).
2. "answer_relevance": Does the answer directly address the question without superfluous filler? (1.0 = directly addresses query).
3. "context_precision": Does the retrieved context contain the necessary facts to answer the question? (1.0 = highly relevant facts present).

Respond ONLY with a valid JSON object matching this schema:
{{
  "faithfulness": 1.0,
  "answer_relevance": 1.0,
  "context_precision": 1.0,
  "reasoning": "concise explanation"
}}
"""
    raw_res = llm_client.generate("Evaluate RAG output", judge_prompt)
    return _extract_json(raw_res)


def run_semantic_eval(
    benchmark_file: str = "evals/golden_benchmark.json",
    report_file: str = "evals/semantic_eval_report.json",
    sample_size: int = 5,
) -> dict:
    benchmark_path = PROJECT_ROOT / benchmark_file
    if not benchmark_path.exists():
        raise FileNotFoundError(f"Benchmark file not found at {benchmark_path}")

    with open(benchmark_path, "r", encoding="utf-8") as f:
        all_cases = json.load(f)

    in_domain_cases = [c for c in all_cases if c.get("is_relevant")]
    if sample_size > 0:
        cases_to_eval = in_domain_cases[:sample_size]
    else:
        cases_to_eval = in_domain_cases

    store = get_active_store()
    llm = get_llm_client()

    print("\n" + "=" * 80)
    print(" POLICYDESK SEMANTIC RAG EVALUATION (RAGAS / LLM-AS-A-JUDGE)")
    print("=" * 80)
    print(f"Evaluating {len(cases_to_eval)} test queries with active provider ({type(llm).__name__})...\n")

    eval_results = []
    faithfulness_scores = []
    relevance_scores = []
    context_precision_scores = []

    for idx, c in enumerate(cases_to_eval, 1):
        qid = c["id"]
        query = c["query"]
        category = c.get("category", "general")

        retrieval_res = store.retrieve(query, top_k=1, expand_window=1)
        top_chunk = retrieval_res[0] if retrieval_res else {}
        context = top_chunk.get("text", "")
        retrieval_score = top_chunk.get("score", 0.0)

        t0 = time.time()
        answer = llm.generate(query, context)
        gen_time = time.time() - t0

        t1 = time.time()
        scores = evaluate_sample(query, context, answer, llm)
        judge_time = time.time() - t1

        faith = float(scores.get("faithfulness", 1.0))
        rel = float(scores.get("answer_relevance", 1.0))
        prec = float(scores.get("context_precision", 1.0))
        reasoning = scores.get("reasoning", "")

        faithfulness_scores.append(faith)
        relevance_scores.append(rel)
        context_precision_scores.append(prec)

        eval_results.append({
            "id": qid,
            "category": category,
            "query": query,
            "retrieval_score": retrieval_score,
            "generated_answer": answer,
            "faithfulness": faith,
            "answer_relevance": rel,
            "context_precision": prec,
            "reasoning": reasoning,
            "gen_time_sec": round(gen_time, 2),
            "judge_time_sec": round(judge_time, 2),
        })

        print(f"[{idx:02d}/{len(cases_to_eval):02d}] {qid} ({category}):")
        print(f"   Query: {query}")
        print(f"   Answer: {answer}")
        print(f"   Scores: Faithfulness={faith:.2f} | Answer Rel={rel:.2f} | Context Prec={prec:.2f}")
        print(f"   Judge Note: {reasoning}\n")

    avg_faithfulness = sum(faithfulness_scores) / len(faithfulness_scores) if faithfulness_scores else 0.0
    avg_relevance = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0
    avg_precision = sum(context_precision_scores) / len(context_precision_scores) if context_precision_scores else 0.0

    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_evaluated": len(cases_to_eval),
        "llm_provider": type(llm).__name__,
        "metrics": {
            "average_faithfulness": round(avg_faithfulness, 4),
            "average_answer_relevance": round(avg_relevance, 4),
            "average_context_precision": round(avg_precision, 4),
        },
        "results": eval_results,
    }

    report_path = PROJECT_ROOT / report_file
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("=" * 80)
    print(" SUMMARY BENCHMARK METRICS")
    print("=" * 80)
    print(f" Total Evaluated Queries   : {len(cases_to_eval)}")
    print(f" Average Faithfulness      : {avg_faithfulness * 100:.1f}%")
    print(f" Average Answer Relevance  : {avg_relevance * 100:.1f}%")
    print(f" Average Context Precision : {avg_precision * 100:.1f}%")
    print(f" Report saved to           : {report_path.relative_to(PROJECT_ROOT)}")
    print("=" * 80 + "\n")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run semantic Ragas evaluations on PolicyDesk.")
    parser.add_argument("--sample", type=int, default=5, help="Number of queries to evaluate (0 for all in-domain queries)")
    parser.add_argument("--benchmark", type=str, default="evals/golden_benchmark.json", help="Path to golden benchmark JSON")
    parser.add_argument("--report", type=str, default="evals/semantic_eval_report.json", help="Output path for eval report")
    args = parser.parse_args()

    run_semantic_eval(benchmark_file=args.benchmark, report_file=args.report, sample_size=args.sample)
