from scripts.evaluate_rag import run_evaluation


class TestRAGEvaluationBenchmark:
    """Automated quantitative RAG benchmark asserting retrieval metrics in CI/CD."""

    def test_rag_benchmark_metrics(self, tmp_path):
        report_out = tmp_path / "eval_report.json"
        metrics = run_evaluation(
            benchmark_file="evals/golden_benchmark.json",
            report_file=str(report_out),
        )

        assert metrics["total_queries"] >= 30
        assert metrics["hit_rate_at_1"] >= 90.0, f"Hit@1 failed target: {metrics['hit_rate_at_1']}%"
        assert metrics["hit_rate_at_3"] >= 95.0, f"Hit@3 failed target: {metrics['hit_rate_at_3']}%"
        assert metrics["mean_reciprocal_rank"] >= 0.90, f"MRR failed target: {metrics['mean_reciprocal_rank']}"
        assert metrics["overall_gating_accuracy"] >= 95.0, f"Gating accuracy failed: {metrics['overall_gating_accuracy']}%"
        assert metrics["gating_specificity_tnr"] >= 90.0, f"OOS Specificity failed: {metrics['gating_specificity_tnr']}%"
        assert metrics["avg_in_domain_cosine_score"] > 0.30
        assert metrics["avg_out_of_scope_cosine_score"] < 0.15

    def test_semantic_eval_sample_and_json_parser(self):
        from app.llm.client import DeterministicMockClient
        from scripts.evaluate_ragas import _extract_json, evaluate_sample

        json_text = '```json\n{"faithfulness": 0.95, "answer_relevance": 0.90, "context_precision": 1.0, "reasoning": "Accurate."}\n```'
        parsed = _extract_json(json_text)
        assert parsed["faithfulness"] == 0.95
        assert parsed["answer_relevance"] == 0.90

        fallback = _extract_json("Plain text with no json")
        assert fallback["faithfulness"] == 1.0
        assert "Plain text" in fallback["reasoning"]

        mock_client = DeterministicMockClient()
        res = evaluate_sample(
            query="What is the fee?",
            context="The fee is $25.",
            answer="The fee is $25.",
            llm_client=mock_client,
        )
        assert "faithfulness" in res
        assert "answer_relevance" in res
