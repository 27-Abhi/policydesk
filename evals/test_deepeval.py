"""Official DeepEval Test Suite for PolicyDesk RAG Pipeline.

Executes quantitative LLM-as-a-judge assertions using DeepEval:
- FaithfulnessMetric: Asserts actual output is entailed by retrieval context.
- AnswerRelevancyMetric: Asserts actual output directly addresses input.

Can be run via:
    deepeval test run evals/test_deepeval.py
or:
    pytest evals/test_deepeval.py
"""

import os
from pathlib import Path
import pytest
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

if not os.environ.get("OPENAI_API_KEY") and os.environ.get("OPEN_AI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ["OPEN_AI_API_KEY"]

from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric

from app.graph.rag_nodes import get_active_store
from app.llm.client import get_llm_client

EVAL_MODEL = os.getenv("OPENAI_MODEL", "gemini-flash-lite-latest")


@pytest.fixture(scope="module")
def rag_pipeline():
    store = get_active_store()
    llm = get_llm_client()
    return store, llm


@pytest.mark.parametrize(
    "query, expected_clause",
    [
        (
            "What is the fee for an international wire transfer?",
            "International wire transfers incur a $45 fee",
        ),
        (
            "How much does a domestic wire transfer cost?",
            "Domestic wire transfers incur a $25 fee",
        ),
        (
            "What is the daily cutoff time for wire transfers?",
            "Wire transfer daily cutoff time is strictly 4:00 PM EST",
        ),
    ],
)
def test_policydesk_rag_deepeval(rag_pipeline, query, expected_clause):
    store, llm = rag_pipeline
    results = store.retrieve(query, top_k=1, expand_window=1)
    assert len(results) > 0, f"No documents retrieved for query: {query}"

    context_text = results[0]["text"]
    actual_answer = llm.generate(query, context_text)

    test_case = LLMTestCase(
        input=query,
        actual_output=actual_answer,
        retrieval_context=[context_text],
    )

    faithfulness = FaithfulnessMetric(threshold=0.7, model=EVAL_MODEL)
    relevancy = AnswerRelevancyMetric(threshold=0.7, model=EVAL_MODEL)

    assert_test(test_case, [faithfulness, relevancy])
