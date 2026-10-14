import uuid
from typing import Any, Optional
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph


from app.graph.dispute_nodes import (
    extract_dispute_details,
    finalize_dispute,
    require_human_approval,
)
from app.graph.nodes import (
    account_placeholder,
    classify_intent,
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


def build_graph(checkpointer: Optional[Any] = None):
    """Construct and compile the PolicyDesk workflow graph with HITL checkpointing."""
    builder = StateGraph(PolicyDeskState)

    builder.add_node("sanitize", sanitize)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("retrieve_parent_chunks", retrieve_parent_chunks)
    builder.add_node("grade_documents", grade_documents)
    builder.add_node("rerank_documents", rerank_documents)
    builder.add_node("generate_answer", generate_answer)
    builder.add_node("extract_dispute_details", extract_dispute_details)
    builder.add_node("require_human_approval", require_human_approval)
    builder.add_node("finalize_dispute", finalize_dispute)
    builder.add_node("account_placeholder", account_placeholder)
    builder.add_node("human_placeholder", human_placeholder)
    builder.add_node("out_of_scope_placeholder", out_of_scope_placeholder)

    builder.add_edge(START, "sanitize")
    builder.add_edge("sanitize", "classify_intent")

    builder.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "retrieve_parent_chunks": "retrieve_parent_chunks",
            "extract_dispute_details": "extract_dispute_details",
            "account_placeholder": "account_placeholder",
            "human_placeholder": "human_placeholder",
            "out_of_scope_placeholder": "out_of_scope_placeholder",
        },
    )

    # 4-step RAG pipeline sequence
    builder.add_edge("retrieve_parent_chunks", "grade_documents")
    builder.add_edge("grade_documents", "rerank_documents")
    builder.add_edge("rerank_documents", "generate_answer")
    builder.add_edge("generate_answer", END)

    # 3-node Dispute HITL sequence
    builder.add_edge("extract_dispute_details", "require_human_approval")
    builder.add_edge("require_human_approval", "finalize_dispute")
    builder.add_edge("finalize_dispute", END)

    builder.add_edge("account_placeholder", END)
    builder.add_edge("human_placeholder", END)
    builder.add_edge("out_of_scope_placeholder", END)

    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = builder.compile(checkpointer=checkpointer)

    # Ensure backwards compatibility for calls without explicit thread_id
    original_invoke = compiled.invoke

    def safe_invoke(input_data: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        if config is None:
            config = {"configurable": {"thread_id": f"session_{uuid.uuid4().hex[:12]}"}}
        elif "configurable" not in config or "thread_id" not in config["configurable"]:
            config = dict(config)
            cfg = dict(config.get("configurable", {}))
            cfg.setdefault("thread_id", f"session_{uuid.uuid4().hex[:12]}")
            config["configurable"] = cfg
        return original_invoke(input_data, config=config, **kwargs)

    compiled.invoke = safe_invoke
    return compiled


