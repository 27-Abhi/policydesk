"""PolicyDesk LangGraph StateGraph assembly with thread-safe SQLite WAL checkpointing."""

import os
import sqlite3
import threading
import uuid
from functools import lru_cache
from typing import Any, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.graph.account_nodes import handle_account_lookup
from app.graph.dispute_nodes import (
    extract_dispute_details,
    finalize_dispute,
    require_human_approval,
)
from app.graph.escalation_nodes import handle_human_escalation
from app.graph.nodes import (
    classify_intent,
    out_of_scope_placeholder,
    sanitize,
)
from app.graph.rag_nodes import (
    abstain_policy_answer,
    generate_answer,
    grade_documents,
    rerank_documents,
    retrieve_parent_chunks,
    rewrite_query,
    verify_grounding,
)
from app.graph.router import (
    route_after_grade,
    route_after_grounding,
    route_after_sanitize,
    route_by_intent,
)
from app.graph.state import PolicyDeskState


class ThreadSafeSqliteSaver(SqliteSaver):
    """SqliteSaver guarded by a re-entrant lock for concurrent FastAPI worker threads."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self._thread_lock = threading.RLock()

    def get_tuple(self, config):
        with self._thread_lock:
            return super().get_tuple(config)

    def list(self, config, *, filter=None, before=None, limit=None):
        with self._thread_lock:
            return list(super().list(config, filter=filter, before=before, limit=limit))

    def put(self, config, checkpoint, metadata, new_versions):
        with self._thread_lock:
            return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        with self._thread_lock:
            return super().put_writes(config, writes, task_id, task_path=task_path)

    def delete_thread(self, thread_id: str) -> None:
        with self._thread_lock:
            return super().delete_thread(thread_id)


def build_graph(checkpointer: Optional[Any] = None):
    """Construct and compile the PolicyDesk agentic workflow graph with HITL checkpointing."""
    builder = StateGraph(PolicyDeskState)

    builder.add_node("sanitize", sanitize)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("retrieve_parent_chunks", retrieve_parent_chunks)
    builder.add_node("grade_documents", grade_documents)
    builder.add_node("rewrite_query", rewrite_query)
    builder.add_node("rerank_documents", rerank_documents)
    builder.add_node("generate_answer", generate_answer)
    builder.add_node("verify_grounding", verify_grounding)
    builder.add_node("abstain_policy_answer", abstain_policy_answer)
    builder.add_node("extract_dispute_details", extract_dispute_details)
    builder.add_node("require_human_approval", require_human_approval)
    builder.add_node("finalize_dispute", finalize_dispute)
    builder.add_node("handle_account_lookup", handle_account_lookup)
    builder.add_node("handle_human_escalation", handle_human_escalation)
    builder.add_node("out_of_scope_placeholder", out_of_scope_placeholder)

    builder.add_edge(START, "sanitize")

    builder.add_conditional_edges(
        "sanitize",
        route_after_sanitize,
        {
            "classify_intent": "classify_intent",
            "out_of_scope_placeholder": "out_of_scope_placeholder",
        },
    )

    builder.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "retrieve_parent_chunks": "retrieve_parent_chunks",
            "extract_dispute_details": "extract_dispute_details",
            "handle_account_lookup": "handle_account_lookup",
            "handle_human_escalation": "handle_human_escalation",
            "out_of_scope_placeholder": "out_of_scope_placeholder",
        },
    )

    # Bounded Corrective RAG (CRAG) pipeline with query rewrite and grounding check
    builder.add_edge("retrieve_parent_chunks", "grade_documents")
    builder.add_conditional_edges(
        "grade_documents",
        route_after_grade,
        {
            "rerank_documents": "rerank_documents",
            "rewrite_query": "rewrite_query",
            "abstain_policy_answer": "abstain_policy_answer",
        },
    )
    builder.add_edge("rewrite_query", "retrieve_parent_chunks")
    builder.add_edge("rerank_documents", "generate_answer")
    builder.add_edge("generate_answer", "verify_grounding")
    builder.add_conditional_edges(
        "verify_grounding",
        route_after_grounding,
        {
            END: END,
            "generate_answer": "generate_answer",
            "abstain_policy_answer": "abstain_policy_answer",
        },
    )
    builder.add_edge("abstain_policy_answer", END)

    # 3-node Dispute HITL sequence
    builder.add_edge("extract_dispute_details", "require_human_approval")
    builder.add_edge("require_human_approval", "finalize_dispute")
    builder.add_edge("finalize_dispute", END)

    builder.add_edge("handle_account_lookup", END)
    builder.add_edge("handle_human_escalation", END)
    builder.add_edge("out_of_scope_placeholder", END)

    if checkpointer is None:
        db_path = os.getenv("CHECKPOINT_DB_PATH", "data/checkpoints.db")
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        checkpointer = ThreadSafeSqliteSaver(conn)

    compiled = builder.compile(checkpointer=checkpointer)

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


@lru_cache(maxsize=1)
def get_graph():
    """Return the cached singleton compiled graph instance."""
    return build_graph()
