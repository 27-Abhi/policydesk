from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from langgraph.graph.state import CompiledStateGraph
from dotenv import load_dotenv

load_dotenv()

from app.api.schemas import PolicyListResponse
from app.api.v1.router import (
    admin_v1_router,
    get_allowed_origins,
    list_policies,
    verify_admin_auth,
)
from app.graph.escalation_nodes import get_escalation_tickets, resolve_escalation_ticket
from app.graph.graph import get_graph

admin_app = FastAPI(
    title="PolicyDesk Compliance & Operations Portal",
    description="Restricted internal staff operations and dispute review dashboard on Port 8001",
    version="0.1.0",
)

admin_app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
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


@admin_app.get(
    "/v1/disputes",
    summary="List all active and processed dispute cases",
    dependencies=[Depends(verify_admin_auth)],
)
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


admin_app.include_router(admin_v1_router, prefix="/v1", dependencies=[Depends(verify_admin_auth)])

admin_app.add_api_route(
    "/v1/policies",
    list_policies,
    methods=["GET"],
    response_model=PolicyListResponse,
    summary="List all indexed policy documents in the active vector store",
    dependencies=[Depends(verify_admin_auth)],
)


@admin_app.get(
    "/v1/escalations",
    summary="List Customer Care support tickets",
    dependencies=[Depends(verify_admin_auth)],
)
def list_escalations():
    return get_escalation_tickets()


@admin_app.post(
    "/v1/escalations/{ticket_id}/resolve",
    summary="Resolve a customer support ticket",
    dependencies=[Depends(verify_admin_auth)],
)
def resolve_ticket(ticket_id: str):
    success = resolve_escalation_ticket(ticket_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found.")
    return {"status": "ok", "message": f"Ticket {ticket_id} marked as resolved."}
