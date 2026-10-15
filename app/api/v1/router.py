import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.graph.graph import get_graph
from app.graph.rag_nodes import get_active_store
from app.prompts import format_dispute_pending, OUT_OF_SCOPE_REFUSAL
from app.api.schemas import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatDebugResponse,
    ChatMessage,
    DeltaMessage,
    DisputeReviewRequest,
    DisputeReviewResponse,
    PolicyDocumentSummary,
    PolicyIngestRequest,
    PolicyIngestResponse,
    PolicyListResponse,
)

api_v1_router = APIRouter(tags=["Chat Completions"])


async def _stream_chat_completion(
    request: ChatCompletionRequest,
    graph: CompiledStateGraph,
    prompt: str,
    thread_id: str,
) -> AsyncGenerator[str, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    msg_history = [{"role": m.role, "content": m.content} for m in request.messages]
    final_state = graph.invoke(
        {"user_input": prompt, "messages": msg_history},
        config={"configurable": {"thread_id": thread_id}},
    )

    if final_state.get("__interrupt__") or final_state.get("dispute_status") == "pending_human_review":
        dispute_id = final_state.get("dispute_id", "PENDING")
        tx_id = final_state.get("transaction_id", "GENERAL")
        answer = (
            f"Dispute case {dispute_id} for transaction {tx_id} has been recorded and submitted for compliance review. "
            f"Reference thread ID: {thread_id}."
        )
    else:
        answer = final_state.get("answer") or OUT_OF_SCOPE_REFUSAL

    # First event announces the assistant role
    initial_chunk = ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=request.model,
        choices=[ChatCompletionChunkChoice(delta=DeltaMessage(role="assistant"))],
    )
    yield f"data: {initial_chunk.model_dump_json()}\n\n"

    # Stream text in word chunks
    words = answer.split(" ")
    for idx, word in enumerate(words):
        token = word if idx == 0 else " " + word
        chunk = ChatCompletionChunk(
            id=completion_id,
            created=created,
            model=request.model,
            choices=[ChatCompletionChunkChoice(delta=DeltaMessage(content=token))],
        )
        yield f"data: {chunk.model_dump_json()}\n\n"

    # Final event signals completion
    final_chunk = ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=request.model,
        choices=[ChatCompletionChunkChoice(delta=DeltaMessage(), finish_reason="stop")],
    )
    yield f"data: {final_chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


@api_v1_router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    summary="OpenAI-compatible chat completion",
)
def create_chat_completion(
    request: ChatCompletionRequest,
    response: Response,
    x_thread_id: Optional[str] = Header(None, alias="X-Thread-ID"),
    graph: CompiledStateGraph = Depends(get_graph),
):
    user_prompts = [m.content.strip() for m in request.messages if m.role == "user" and m.content.strip()]
    if not user_prompts:
        raise HTTPException(
            status_code=400,
            detail="Request must contain at least one non-empty user message.",
        )

    last_user_prompt = user_prompts[-1]
    thread_id = x_thread_id or f"session_{uuid.uuid4().hex[:12]}"
    response.headers["X-Thread-ID"] = thread_id

    if request.stream:
        return StreamingResponse(
            _stream_chat_completion(request, graph, last_user_prompt, thread_id),
            media_type="text/event-stream",
            headers={"X-Thread-ID": thread_id},
        )

    msg_history = [{"role": m.role, "content": m.content} for m in request.messages]
    final_state = graph.invoke(
        {"user_input": last_user_prompt, "messages": msg_history},
        config={"configurable": {"thread_id": thread_id}},
    )

    if final_state.get("__interrupt__") or final_state.get("dispute_status") == "pending_human_review":
        dispute_id = final_state.get("dispute_id", "PENDING")
        tx_id = final_state.get("transaction_id", "GENERAL")
        answer = format_dispute_pending(dispute_id=dispute_id, transaction_id=tx_id, thread_id=thread_id)
    else:
        answer = final_state.get("answer") or OUT_OF_SCOPE_REFUSAL

    return ChatCompletionResponse(
        model=request.model,
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(role="assistant", content=answer),
                finish_reason="stop",
            )
        ],
    )


@api_v1_router.get(
    "/disputes/{thread_id}",
    summary="Get dispute case details and review status",
)
def get_dispute_status(
    thread_id: str,
    graph: CompiledStateGraph = Depends(get_graph),
):
    checkpoint_state = graph.get_state({"configurable": {"thread_id": thread_id}})
    if not checkpoint_state or not checkpoint_state.values:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute case for thread_id '{thread_id}' was not found.",
        )
    values = checkpoint_state.values
    is_pending = bool(checkpoint_state.next)
    return {
        "thread_id": thread_id,
        "dispute_id": values.get("dispute_id"),
        "transaction_id": values.get("transaction_id"),
        "dispute_status": values.get("dispute_status"),
        "dispute_comments": values.get("dispute_comments"),
        "dispute_notification_sent": values.get("dispute_notification_sent"),
        "is_pending_review": is_pending,
        "user_input": values.get("user_input"),
        "answer": values.get("answer"),
    }


@api_v1_router.get(
    "/disputes/{thread_id}/events",
    summary="Real-time Server-Sent Events stream for dispute status resolution",
)
async def stream_dispute_events(
    thread_id: str,
    graph: CompiledStateGraph = Depends(get_graph),
):
    async def event_generator() -> AsyncGenerator[str, None]:
        # Announce successful SSE handshake
        yield f"data: {json.dumps({'type': 'connected', 'thread_id': thread_id})}\n\n"

        # Check resolution up to 10 minutes (600 iterations @ 1s)
        for _ in range(600):
            checkpoint_state = graph.get_state({"configurable": {"thread_id": thread_id}})
            if checkpoint_state and checkpoint_state.values:
                # If there are no pending next nodes, execution has finalized
                if not checkpoint_state.next:
                    vals = checkpoint_state.values
                    status = vals.get("dispute_status")
                    if status and status != "pending_human_review":
                        payload = {
                            "type": "dispute_resolved",
                            "thread_id": thread_id,
                            "dispute_id": vals.get("dispute_id"),
                            "transaction_id": vals.get("transaction_id"),
                            "dispute_status": status,
                            "dispute_comments": vals.get("dispute_comments"),
                            "answer": vals.get("answer"),
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                        return

            await asyncio.sleep(1)

        yield f"data: {json.dumps({'type': 'timeout', 'thread_id': thread_id})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@api_v1_router.post(
    "/disputes/{thread_id}/review",
    response_model=DisputeReviewResponse,
    summary="Human-in-the-loop review to approve or reject a dispute",
)
def review_dispute(
    thread_id: str,
    review: DisputeReviewRequest,
    graph: CompiledStateGraph = Depends(get_graph),
):
    checkpoint_state = graph.get_state({"configurable": {"thread_id": thread_id}})
    if not checkpoint_state or not checkpoint_state.values:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute case for thread_id '{thread_id}' was not found.",
        )

    if not checkpoint_state.next:
        status = checkpoint_state.values.get("dispute_status", "completed")
        raise HTTPException(
            status_code=400,
            detail=f"Dispute thread '{thread_id}' has already been processed with status '{status}'.",
        )

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


@api_v1_router.post(
    "/chat/debug",
    response_model=ChatDebugResponse,
    summary="Observability endpoint returning raw state, derived chunks, and scores",
)
def debug_chat_query(
    request: ChatCompletionRequest,
    graph: CompiledStateGraph = Depends(get_graph),
):
    user_prompts = [m.content.strip() for m in request.messages if m.role == "user" and m.content.strip()]
    if not user_prompts:
        raise HTTPException(
            status_code=400,
            detail="Request must contain at least one non-empty user message.",
        )

    last_user_prompt = user_prompts[-1]
    final_state = graph.invoke({"user_input": last_user_prompt})

    intent_val = final_state.get("intent")
    if hasattr(intent_val, "value"):
        intent_str = intent_val.value
    elif intent_val:
        intent_str = str(intent_val)
    else:
        intent_str = None

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


@api_v1_router.get(
    "/policies",
    response_model=PolicyListResponse,
    summary="List all indexed policy documents in the active vector store",
)
def list_policies():
    store = get_active_store()
    docs = store.list_documents()
    return PolicyListResponse(
        total_documents=len(docs),
        total_parents=len(store.parents),
        total_children=len(store.children),
        policies=[PolicyDocumentSummary(**d) for d in docs],
    )


@api_v1_router.post(
    "/policies/ingest",
    response_model=PolicyIngestResponse,
    summary="Ingest a new policy document into the vector store via JSON payload",
)
def ingest_policy_json(request: PolicyIngestRequest):
    store = get_active_store()

    # Persist policy text to data/policies/ so it reloads on restarts
    policies_dir = Path("data/policies")
    policies_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = f"{request.doc_id}.txt"
    (policies_dir / safe_filename).write_text(request.text, encoding="utf-8")

    parents_added, children_added = store.add_document(doc_id=request.doc_id, title=request.title, text=request.text)

    return PolicyIngestResponse(
        status="success",
        message=f"Policy '{request.doc_id}' successfully ingested and indexed.",
        doc_id=request.doc_id,
        title=request.title,
        parents_added=parents_added,
        children_added=children_added,
        total_parents=len(store.parents),
        total_children=len(store.children),
    )


@api_v1_router.post(
    "/policies/upload",
    response_model=PolicyIngestResponse,
    summary="Upload and ingest a policy file (.pdf, .txt, .md) into the vector store",
)
async def upload_policy_file(
    file: UploadFile = File(...),
    doc_id: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
):
    policies_dir = Path("data/policies")
    policies_dir.mkdir(parents=True, exist_ok=True)

    file_bytes = await file.read()
    filename = file.filename or "uploaded_policy.txt"
    dest_path = policies_dir / filename
    dest_path.write_bytes(file_bytes)

    if filename.lower().endswith(".pdf"):
        from app.rag.ingest import load_pdf_text
        text = load_pdf_text(dest_path)
    else:
        text = file_bytes.decode("utf-8", errors="replace")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Uploaded file contains no readable text.")

    final_doc_id = doc_id.strip() if doc_id and doc_id.strip() else Path(filename).stem.lower().replace(" ", "_")
    final_title = title.strip() if title and title.strip() else Path(filename).stem.replace("_", " ").title()

    store = get_active_store()
    parents_added, children_added = store.add_document(doc_id=final_doc_id, title=final_title, text=text)

    return PolicyIngestResponse(
        status="success",
        message=f"File '{filename}' successfully ingested as '{final_doc_id}'.",
        doc_id=final_doc_id,
        title=final_title,
        parents_added=parents_added,
        children_added=children_added,
        total_parents=len(store.parents),
        total_children=len(store.children),
    )

