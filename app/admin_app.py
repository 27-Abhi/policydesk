import os
from pathlib import Path
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from dotenv import load_dotenv

load_dotenv()

from app.api.deps import get_graph
from app.api.schemas import (
    ChatCompletionRequest,
    ChatDebugResponse,
    DisputeReviewRequest,
    DisputeReviewResponse,
)
from app.graph.escalation_nodes import get_escalation_tickets, resolve_escalation_ticket

admin_app = FastAPI(
    title="PolicyDesk Compliance & Operations Portal",
    description="Restricted internal staff operations and dispute review dashboard on Port 8001",
    version="0.1.0",
)

admin_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@admin_app.get("/", response_class=HTMLResponse, tags=["Compliance Web App"])
def serve_compliance_portal():
    """Serve the internal compliance operations dashboard."""
    html_file = STATIC_DIR / "compliance.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>PolicyDesk Compliance Portal</h1><p>static/compliance.html not found</p>")


@admin_app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "policydesk-compliance-portal", "port": 8001}


@admin_app.get("/v1/disputes", summary="List all active and processed dispute cases")
def list_dispute_cases(graph: CompiledStateGraph = Depends(get_graph)):
    """Query checkpointer storage and return all recorded dispute threads."""
    thread_ids = set()
    for checkpoint_tuple in graph.checkpointer.list(None):
        cfg = getattr(checkpoint_tuple, "config", {})
        tid = cfg.get("configurable", {}).get("thread_id")
        if tid:
            thread_ids.add(tid)

    disputes = []
    for tid in sorted(thread_ids, reverse=True):
        checkpoint_state = graph.get_state({"configurable": {"thread_id": tid}})
        if not checkpoint_state or not checkpoint_state.values:
            continue
        vals = checkpoint_state.values
        dispute_id = vals.get("dispute_id")
        if not dispute_id:
            continue
        is_pending = bool(checkpoint_state.next)
        disputes.append({
            "thread_id": tid,
            "dispute_id": dispute_id,
            "transaction_id": vals.get("transaction_id", "N/A"),
            "dispute_status": vals.get("dispute_status", "unknown"),
            "dispute_comments": vals.get("dispute_comments"),
            "dispute_notification_sent": vals.get("dispute_notification_sent", False),
            "is_pending_review": is_pending,
            "user_input": vals.get("user_input"),
            "answer": vals.get("answer"),
        })
    return disputes


@admin_app.get("/v1/disputes/{thread_id}", summary="Get dispute case details")
def get_dispute_status(thread_id: str, graph: CompiledStateGraph = Depends(get_graph)):
    checkpoint_state = graph.get_state({"configurable": {"thread_id": thread_id}})
    if not checkpoint_state or not checkpoint_state.values:
        raise HTTPException(status_code=404, detail=f"Dispute case for thread_id '{thread_id}' not found.")
    vals = checkpoint_state.values
    return {
        "thread_id": thread_id,
        "dispute_id": vals.get("dispute_id"),
        "transaction_id": vals.get("transaction_id"),
        "dispute_status": vals.get("dispute_status"),
        "dispute_comments": vals.get("dispute_comments"),
        "dispute_notification_sent": vals.get("dispute_notification_sent"),
        "is_pending_review": bool(checkpoint_state.next),
        "user_input": vals.get("user_input"),
        "answer": vals.get("answer"),
    }


@admin_app.post("/v1/disputes/{thread_id}/review", response_model=DisputeReviewResponse, summary="Review dispute case")
def review_dispute(
    thread_id: str,
    review: DisputeReviewRequest,
    graph: CompiledStateGraph = Depends(get_graph),
):
    checkpoint_state = graph.get_state({"configurable": {"thread_id": thread_id}})
    if not checkpoint_state or not checkpoint_state.values:
        raise HTTPException(status_code=404, detail=f"Dispute case for thread_id '{thread_id}' not found.")

    if not checkpoint_state.next:
        status = checkpoint_state.values.get("dispute_status", "completed")
        raise HTTPException(status_code=400, detail=f"Dispute thread '{thread_id}' has already been processed with status '{status}'.")

    resumed_state = graph.invoke(
        Command(resume={"approved": review.approved, "comments": review.comments or ""}),
        config={"configurable": {"thread_id": thread_id}},
    )

    return DisputeReviewResponse(
        thread_id=thread_id,
        dispute_id=resumed_state.get("dispute_id", ""),
        transaction_id=resumed_state.get("transaction_id", ""),
        dispute_status=resumed_state.get("dispute_status", ""),
        dispute_comments=resumed_state.get("dispute_comments"),
        dispute_notification_sent=bool(resumed_state.get("dispute_notification_sent", False)),
        answer=resumed_state.get("answer", ""),
    )


@admin_app.get("/v1/escalations", summary="List Customer Care support tickets")
def list_escalations():
    return get_escalation_tickets()


@admin_app.post("/v1/escalations/{ticket_id}/resolve", summary="Resolve a customer support ticket")
def resolve_ticket(ticket_id: str):
    success = resolve_escalation_ticket(ticket_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found.")
    return {"status": "ok", "message": f"Ticket {ticket_id} marked as resolved."}


@admin_app.post("/v1/chat/debug", response_model=ChatDebugResponse, summary="RAG pipeline inspection")
def debug_chat_query(request: ChatCompletionRequest, graph: CompiledStateGraph = Depends(get_graph)):
    user_prompts = [m.content.strip() for m in request.messages if m.role == "user" and m.content.strip()]
    if not user_prompts:
        raise HTTPException(status_code=400, detail="Request must contain at least one user message.")

    last_user_prompt = user_prompts[-1]
    final_state = graph.invoke({"user_input": last_user_prompt})

    intent_val = final_state.get("intent")
    intent_str = intent_val.value if hasattr(intent_val, "value") else (str(intent_val) if intent_val else None)

    return ChatDebugResponse(
        user_input=final_state.get("user_input", last_user_prompt),
        intent=intent_str,
        route=final_state.get("route"),
        is_relevant=final_state.get("is_relevant"),
        relevance_score=final_state.get("relevance_score"),
        retrieved_docs=final_state.get("retrieved_docs") or [],
        reranked_docs=final_state.get("reranked_docs") or [],
        answer=final_state.get("answer"),
        errors=final_state.get("errors") or [],
    )
