"""LangGraph construction for PolicyDesk workflow."""

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    account_placeholder,
    classify_intent,
    dispute_placeholder,
    human_placeholder,
    out_of_scope_placeholder,
    policy_placeholder,
    sanitize,
)
from app.graph.router import route_by_intent
from app.graph.state import PolicyDeskState


def build_graph():
    """Construct and compile the PolicyDesk workflow graph.

    Workflow:
        START -> sanitize -> classify_intent -> (conditional branch) -> placeholder -> END
    """
    builder = StateGraph(PolicyDeskState)

    builder.add_node("sanitize", sanitize)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("policy_placeholder", policy_placeholder)
    builder.add_node("account_placeholder", account_placeholder)
    builder.add_node("dispute_placeholder", dispute_placeholder)
    builder.add_node("human_placeholder", human_placeholder)
    builder.add_node("out_of_scope_placeholder", out_of_scope_placeholder)

    builder.add_edge(START, "sanitize")
    builder.add_edge("sanitize", "classify_intent")

    builder.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "policy_placeholder": "policy_placeholder",
            "account_placeholder": "account_placeholder",
            "dispute_placeholder": "dispute_placeholder",
            "human_placeholder": "human_placeholder",
            "out_of_scope_placeholder": "out_of_scope_placeholder",
        },
    )

    builder.add_edge("policy_placeholder", END)
    builder.add_edge("account_placeholder", END)
    builder.add_edge("dispute_placeholder", END)
    builder.add_edge("human_placeholder", END)
    builder.add_edge("out_of_scope_placeholder", END)

    return builder.compile()
