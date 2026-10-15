import pytest

from app.graph.nodes import (
    classify_intent,
    out_of_scope_placeholder,
    sanitize,
)
from app.graph.state import Intent


class TestSanitizeNode:
    def test_normal_input_trimmed(self):
        state = {"user_input": "  What is the policy?  "}
        result = sanitize(state)
        assert result["user_input"] == "What is the policy?"
        assert "errors" not in result

    def test_empty_input_produces_error(self):
        state = {"user_input": ""}
        result = sanitize(state)
        assert result["user_input"] == ""
        assert len(result["errors"]) == 1
        assert "empty or blank" in result["errors"][0]

    def test_whitespace_only_produces_error(self):
        state = {"user_input": "   \n\t  "}
        result = sanitize(state)
        assert result["user_input"] == ""
        assert len(result["errors"]) == 1
        assert "empty or blank" in result["errors"][0]

    def test_non_string_input_produces_error(self):
        state = {"user_input": None}
        result = sanitize(state)
        assert result["user_input"] == ""
        assert len(result["errors"]) == 1
        assert "non-empty string" in result["errors"][0]


class TestClassifyIntentNode:
    @pytest.mark.parametrize(
        ("prompt", "expected_intent"),
        [
            ("What is the transaction dispute policy?", Intent.POLICY_QUESTION),
            ("What is my account balance?", Intent.ACCOUNT_LOOKUP),
            ("I want to dispute transaction TX123", Intent.DISPUTE_ACTION),
            ("I want to speak to a human", Intent.HUMAN_REQUEST),
            ("Tell me a joke", Intent.OUT_OF_SCOPE),
        ],
    )
    def test_deterministic_intent_classification(self, prompt, expected_intent):
        state = {"user_input": prompt}
        result = classify_intent(state)
        assert result["intent"] == expected_intent

    def test_existing_errors_fail_to_out_of_scope(self):
        state = {"user_input": "", "errors": ["Input was empty."]}
        result = classify_intent(state)
        assert result["intent"] == Intent.OUT_OF_SCOPE


class TestPlaceholderNodes:
    def test_out_of_scope_placeholder(self):
        res = out_of_scope_placeholder({})
        assert res["route"] == "out_of_scope"
        assert "PolicyDesk Banking Assistant" in res["answer"]
        assert "Wire Transfers" in res["answer"]

    def test_sanitize_resets_ephemeral_fields(self):
        state = {
            "user_input": "New turn input",
            "answer": "Old answer from Turn 1",
            "route": "policy_question",
            "retrieved_docs": ["Old chunk"],
            "reranked_docs": ["Old chunk"],
            "relevance_score": 0.95,
            "is_relevant": True,
        }
        res = sanitize(state)
        assert res["user_input"] == "New turn input"
        assert res["answer"] is None
        assert res["route"] is None
        assert res["retrieved_docs"] == []
        assert res["reranked_docs"] == []
        assert res["relevance_score"] == 0.0
        assert res["is_relevant"] is False
