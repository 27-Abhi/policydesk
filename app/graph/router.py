"""Conditional routing logic for PolicyDesk."""

from langgraph.graph import END

from app.graph.rag_nodes import MAX_GENERATION_ATTEMPTS, MAX_RETRIEVAL_ATTEMPTS
from app.graph.state import Intent, PolicyDeskState

ROUTE_MAP = {
    Intent.POLICY_QUESTION: "retrieve_parent_chunks",
    Intent.ACCOUNT_LOOKUP: "handle_account_lookup",
    Intent.DISPUTE_ACTION: "extract_dispute_details",
    Intent.HUMAN_REQUEST: "handle_human_escalation",
    Intent.OUT_OF_SCOPE: "out_of_scope_placeholder",
}

FALLBACK_ROUTE = "out_of_scope_placeholder"


def route_after_sanitize(state: PolicyDeskState) -> str:
    """Short-circuit blocked or malformed inputs directly to safe fallback."""
    if state.get("injection_detected") or state.get("errors"):
        return FALLBACK_ROUTE
    return "classify_intent"


def route_by_intent(state: PolicyDeskState) -> str:
    """Determine the next graph node based on state['intent'].

    Guaranteed total routing: any unknown or missing intent falls back
    to 'out_of_scope_placeholder'.
    """
    intent = state.get("intent")
    return ROUTE_MAP.get(intent, FALLBACK_ROUTE)


def route_after_grade(state: PolicyDeskState) -> str:
    """Branch between reranking, bounded CRAG query rewrite, or abstention."""
    if state.get("is_relevant"):
        return "rerank_documents"
    attempts = int(state.get("retrieval_attempts") or 0)
    if attempts < MAX_RETRIEVAL_ATTEMPTS:
        return "rewrite_query"
    return "abstain_policy_answer"


def route_after_grounding(state: PolicyDeskState) -> str:
    """Branch after post-generation grounding verification with bounded retry."""
    if state.get("grounded"):
        return END
    attempts = int(state.get("generation_attempts") or 0)
    if attempts < MAX_GENERATION_ATTEMPTS:
        return "generate_answer"
    return "abstain_policy_answer"
