"""LangGraph construction for PolicyDesk workflow."""

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    account_placeholder,
    classify_intent,
    dispute_placeholder,
    human_placeholder,
    out_of_scope_placeholder,
    sanitize,
)
from app.graph.rag_nodes import (
    generate_answer,
    grade_documents,
    rerank_documents,
    retrieve_parent_chunks,
)
from app.graph.router import route_by_intent
from app.graph.state import PolicyDeskState


def build_graph():
    """Construct and compile the PolicyDesk workflow graph."""
    builder = StateGraph(PolicyDeskState)

    builder.add_node("sanitize", sanitize)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("retrieve_parent_chunks", retrieve_parent_chunks)
    builder.add_node("grade_documents", grade_documents)
    builder.add_node("rerank_documents", rerank_documents)
    builder.add_node("generate_answer", generate_answer)
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
            "retrieve_parent_chunks": "retrieve_parent_chunks",
            "account_placeholder": "account_placeholder",
            "dispute_placeholder": "dispute_placeholder",
            "human_placeholder": "human_placeholder",
            "out_of_scope_placeholder": "out_of_scope_placeholder",
        },
    )

    # 4-step RAG pipeline sequence
    builder.add_edge("retrieve_parent_chunks", "grade_documents")
    builder.add_edge("grade_documents", "rerank_documents")
    builder.add_edge("rerank_documents", "generate_answer")
    builder.add_edge("generate_answer", END)

    builder.add_edge("account_placeholder", END)
    builder.add_edge("dispute_placeholder", END)
    builder.add_edge("human_placeholder", END)
    builder.add_edge("out_of_scope_placeholder", END)

    return builder.compile()

