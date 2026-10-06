import pytest

from app.graph.router import FALLBACK_ROUTE, route_by_intent
from app.graph.state import Intent


class TestRouter:
    @pytest.mark.parametrize(
        ("intent", "expected_node"),
        [
            (Intent.POLICY_QUESTION, "policy_placeholder"),
            (Intent.ACCOUNT_LOOKUP, "account_placeholder"),
            (Intent.DISPUTE_ACTION, "dispute_placeholder"),
            (Intent.HUMAN_REQUEST, "human_placeholder"),
            (Intent.OUT_OF_SCOPE, "out_of_scope_placeholder"),
            ("policy_question", "policy_placeholder"),
            ("account_lookup", "account_placeholder"),
            ("dispute_action", "dispute_placeholder"),
            ("human_request", "human_placeholder"),
            ("out_of_scope", "out_of_scope_placeholder"),
        ],
    )
    def test_valid_intents_route_correctly(self, intent, expected_node):
        state = {"intent": intent}
        assert route_by_intent(state) == expected_node

    @pytest.mark.parametrize(
        "invalid_intent",
        [
            "unknown_intent",
            "malicious_route",
            123,
            None,
        ],
    )
    def test_invalid_intents_fallback_safely(self, invalid_intent):
        state = {"intent": invalid_intent}
        assert route_by_intent(state) == FALLBACK_ROUTE

    def test_empty_state_falls_back_safely(self):
        assert route_by_intent({}) == FALLBACK_ROUTE
