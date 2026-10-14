"""Conditional routing logic for PolicyDesk."""

from app.graph.state import Intent, PolicyDeskState

ROUTE_MAP = {
    Intent.POLICY_QUESTION: "retrieve_parent_chunks",
    Intent.ACCOUNT_LOOKUP: "handle_account_lookup",
    Intent.DISPUTE_ACTION: "extract_dispute_details",
    Intent.HUMAN_REQUEST: "handle_human_escalation",
    Intent.OUT_OF_SCOPE: "out_of_scope_placeholder",
}

FALLBACK_ROUTE = "out_of_scope_placeholder"


def route_by_intent(state: PolicyDeskState) -> str:
    """Determine the next graph node based on state['intent'].

    Guaranteed total routing: any unknown or missing intent falls back
    to 'out_of_scope_placeholder'.
    """
    intent = state.get("intent")
    return ROUTE_MAP.get(intent, FALLBACK_ROUTE)
