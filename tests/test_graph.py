import pytest

from app.graph.graph import build_graph
from app.graph.state import Intent


@pytest.fixture(scope="module")
def compiled_graph():
    return build_graph()


class TestGraphExecution:
    @pytest.mark.parametrize(
        ("prompt", "expected_intent", "expected_route"),
        [
            ("What is the transaction dispute policy?", Intent.POLICY_QUESTION, "policy_question"),
            ("What is my account balance?", Intent.ACCOUNT_LOOKUP, "account_lookup"),
            ("I want to dispute transaction TX123", Intent.DISPUTE_ACTION, "dispute_action"),
            ("I want to speak to a human", Intent.HUMAN_REQUEST, "human_request"),
            ("Tell me a joke", Intent.OUT_OF_SCOPE, "out_of_scope"),
        ],
    )
    def test_end_to_end_routing(self, compiled_graph, prompt, expected_intent, expected_route):
        final_state = compiled_graph.invoke({"user_input": prompt})

        assert final_state["user_input"] == prompt.strip()
        assert final_state["intent"] == expected_intent
        assert final_state["route"] == expected_route
        assert "errors" not in final_state or len(final_state["errors"]) == 0

    def test_empty_input_routes_to_out_of_scope_with_error(self, compiled_graph):
        final_state = compiled_graph.invoke({"user_input": "   "})

        assert final_state["user_input"] == ""
        assert final_state["intent"] == Intent.OUT_OF_SCOPE
        assert final_state["route"] == "out_of_scope"
        assert len(final_state["errors"]) == 1
