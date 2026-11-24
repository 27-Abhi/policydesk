import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import anyio
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.api.schemas import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
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
from app.events import get_dispute_event_bus
from app.graph.graph import get_graph
from app.graph.rag_nodes import _verify_numeric_grounding, get_active_store
from app.llm.client import DeterministicMockClient, get_llm_client
from app.observability import estimate_tokens
from app.prompts import OUT_OF_SCOPE_REFUSAL, format_dispute_pending
from app.rag.ingest import load_pdf_text, sanitize_upload_filename

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".txt", ".md"}

customer_v1_router = APIRouter(tags=["Customer Chat & Policy API"])
admin_v1_router = APIRouter(tags=["Compliance & Admin API"])
api_v1_router = APIRouter(tags=["Combined V1 API"])


def get_allowed_origins() -> list[str]:
    """Read allowed CORS origins from environment with safe local defaults."""
    raw = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:8000,http://localhost:8001,http://127.0.0.1:8000,http://127.0.0.1:8001",
    )
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["http://localhost:8000", "http://localhost:8001"]


def verify_admin_auth(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> None:
    """Enforce admin API key authentication when ADMIN_API_KEY is configured."""
    expected = os.getenv("ADMIN_API_KEY", "").strip()
    if not expected:
        return

    token = (x_admin_key or "").strip()
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing admin credentials.")


def _extract_usage(prompt: str, answer: str, final_state: dict[str, Any]) -> ChatCompletionUsage:
    metrics = final_state.get("metrics") or {}
    usage = metrics.get("usage") if isinstance(metrics, dict) else None
    if isinstance(usage, dict) and usage.get("total_tokens"):
        return ChatCompletionUsage(
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
        )
    pt = estimate_tokens(prompt)
    ct = estimate_tokens(answer)
    return ChatCompletionUsage(prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct)


def build_debug_response(final_state: dict[str, Any], fallback_prompt: str) -> ChatDebugResponse:
    """Construct a ChatDebugResponse from final graph state (shared by admin_app and router)."""
    intent_val = final_state.get("intent")
    if hasattr(intent_val, "value"):
        intent_str = intent_val.value
    elif intent_val:
        intent_str = str(intent_val)
    else:
        intent_str = None

    return ChatDebugResponse(
        user_input=final_state.get("user_input", fallback_prompt),
        intent=intent_str,
        route=final_state.get("route"),
        is_relevant=final_state.get("is_relevant"),
        relevance_score=final_state.get("relevance_score"),
        retrieval_attempts=int(final_state.get("retrieval_attempts") or 0),
        generation_attempts=int(final_state.get("generation_attempts") or 0),
        rewritten_query=final_state.get("rewritten_query"),
        grounded=final_state.get("grounded"),
        retrieved_docs=final_state.get("retrieved_docs") or [],
        reranked_docs=final_state.get("reranked_docs") or [],
        citations=final_state.get("citations") or [],
        metrics=final_state.get("metrics") or {},
        answer=final_state.get("answer"),
        errors=final_state.get("errors") or [],
    )


async def _stream_live_tokens(client: Any, query: str, context: str) -> AsyncGenerator[str, None]:
    """Bridge synchronous httpx token streaming into an async generator without blocking the loop."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

    def _produce() -> None:
        try:
            for token in client.stream(query=query, context=context):
                loop.call_soon_threadsafe(queue.put_nowait, token)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    producer_task = loop.run_in_executor(None, _produce)
    try:
        while True:
            tok = await queue.get()
            if tok is None:
                break
            yield tok
    finally:
        await producer_task


async def _stream_chat_completion(
    request: ChatCompletionRequest,
    graph: CompiledStateGraph,
    prompt: str,
    thread_id: str,
) -> AsyncGenerator[str, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    client = get_llm_client()
    use_live_stream = not isinstance(client, DeterministicMockClient)
    msg_history = [{"role": m.role, "content": m.content} for m in request.messages]

    final_state = await anyio.to_thread.run_sync(
        lambda: graph.invoke(
            {
                "user_input": prompt,
                "messages": msg_history,
                "defer_generation": use_live_stream,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
    )

    initial_chunk = ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=request.model,
        choices=[ChatCompletionChunkChoice(delta=DeltaMessage(role="assistant"))],
    )
    yield f"data: {initial_chunk.model_dump_json()}\n\n"

    is_deferred_rag = (
        use_live_stream
        and final_state.get("route") == "policy_question"
        and final_state.get("is_relevant")
        and final_state.get("reranked_docs")
    )

    if is_deferred_rag:
        context = final_state.get("answer") or ""
        streamed_parts: list[str] = []
        async for token in _stream_live_tokens(client, prompt, context):
            streamed_parts.append(token)
            chunk = ChatCompletionChunk(
                id=completion_id,
                created=created,
                model=request.model,
                choices=[ChatCompletionChunkChoice(delta=DeltaMessage(content=token))],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"

        full_text = "".join(streamed_parts)
        evidence = "\n\n".join((final_state.get("reranked_docs") or [])[:3])
        ok, reason = _verify_numeric_grounding(full_text, evidence)
        if not ok:
            note = f"\n\n[Grounding Notice: {reason}]"
            warn_chunk = ChatCompletionChunk(
                id=completion_id,
                created=created,
                model=request.model,
                choices=[ChatCompletionChunkChoice(delta=DeltaMessage(content=note))],
            )
            yield f"data: {warn_chunk.model_dump_json()}\n\n"
    else:
        if final_state.get("__interrupt__") or final_state.get("dispute_status") == "pending_human_review":
            dispute_id = final_state.get("dispute_id", "PENDING")
            tx_id = final_state.get("transaction_id", "GENERAL")
            answer = format_dispute_pending(dispute_id=dispute_id, transaction_id=tx_id, thread_id=thread_id)
        else:
            answer = final_state.get("answer") or OUT_OF_SCOPE_REFUSAL

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

    final_chunk = ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=request.model,
        choices=[ChatCompletionChunkChoice(delta=DeltaMessage(), finish_reason="stop")],
    )
    yield f"data: {final_chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


@customer_v1_router.post(
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
        {"user_input": last_user_prompt, "messages": msg_history, "defer_generation": False},
        config={"configurable": {"thread_id": thread_id}},
    )

    if final_state.get("__interrupt__") or final_state.get("dispute_status") == "pending_human_review":
        dispute_id = final_state.get("dispute_id", "PENDING")
        tx_id = final_state.get("transaction_id", "GENERAL")
        answer = format_dispute_pending(dispute_id=dispute_id, transaction_id=tx_id, thread_id=thread_id)
    else:
        answer = final_state.get("answer") or OUT_OF_SCOPE_REFUSAL

    usage = _extract_usage(last_user_prompt, answer, final_state)
    return ChatCompletionResponse(
        model=request.model,
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(role="assistant", content=answer),
                finish_reason="stop",
            )
        ],
        usage=usage,
    )


@customer_v1_router.get(
    "/disputes/{thread_id}/events",
    summary="Real-time event-driven SSE stream for dispute status resolution",
)
async def stream_dispute_events(
    thread_id: str,
    graph: CompiledStateGraph = Depends(get_graph),
):
    bus = get_dispute_event_bus()

    async def event_generator() -> AsyncGenerator[str, None]:
        queue = await bus.subscribe(thread_id)
        try:
            yield f"data: {json.dumps({'type': 'connected', 'thread_id': thread_id})}\n\n"

            # Emit immediately if already resolved before SSE connection opened
            checkpoint_state = await anyio.to_thread.run_sync(
                lambda: graph.get_state({"configurable": {"thread_id": thread_id}})
            )
            if checkpoint_state and checkpoint_state.values and not checkpoint_state.next:
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

            try:
                event_payload = await asyncio.wait_for(queue.get(), timeout=600.0)
                yield f"data: {json.dumps(event_payload)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'timeout', 'thread_id': thread_id})}\n\n"
        finally:
            bus.unsubscribe(thread_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@customer_v1_router.get(
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


@admin_v1_router.get(
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


@admin_v1_router.post(
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

    payload = {
        "type": "dispute_resolved",
        "thread_id": thread_id,
        "dispute_id": resumed_state.get("dispute_id", ""),
        "transaction_id": resumed_state.get("transaction_id", ""),
        "dispute_status": resumed_state.get("dispute_status", ""),
        "dispute_comments": resumed_state.get("dispute_comments"),
        "answer": resumed_state.get("answer", ""),
    }
    get_dispute_event_bus().publish(thread_id, payload)

    return DisputeReviewResponse(
        thread_id=thread_id,
        dispute_id=resumed_state.get("dispute_id", ""),
        transaction_id=resumed_state.get("transaction_id", ""),
        dispute_status=resumed_state.get("dispute_status", ""),
        dispute_comments=resumed_state.get("dispute_comments"),
        dispute_notification_sent=bool(resumed_state.get("dispute_notification_sent", False)),
        answer=resumed_state.get("answer", ""),
    )


@admin_v1_router.post(
    "/chat/debug",
    response_model=ChatDebugResponse,
    summary="Observability endpoint returning raw state, citations, CRAG metrics, and scores",
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
    final_state = graph.invoke({"user_input": last_user_prompt, "defer_generation": False})
    return build_debug_response(final_state, last_user_prompt)


@admin_v1_router.post(
    "/policies/ingest",
    response_model=PolicyIngestResponse,
    summary="Ingest a new policy document into the vector store via JSON payload",
)
def ingest_policy_json(request: PolicyIngestRequest):
    store = get_active_store()
    safe_doc_id = sanitize_upload_filename(request.doc_id).replace(".", "_")

    policies_dir = Path("data/policies")
    policies_dir.mkdir(parents=True, exist_ok=True)
    (policies_dir / f"{safe_doc_id}.txt").write_text(request.text, encoding="utf-8")

    parents_added, children_added = store.add_document(doc_id=safe_doc_id, title=request.title, text=request.text)

    return PolicyIngestResponse(
        status="success",
        message=f"Policy '{safe_doc_id}' successfully ingested and indexed.",
        doc_id=safe_doc_id,
        title=request.title,
        parents_added=parents_added,
        children_added=children_added,
        total_parents=len(store.parents),
        total_children=len(store.children),
    )


@admin_v1_router.post(
    "/policies/upload",
    response_model=PolicyIngestResponse,
    summary="Upload and ingest a policy file (.pdf, .txt, .md) into the vector store",
)
async def upload_policy_file(
    file: UploadFile = File(...),
    doc_id: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
):
    safe_name = sanitize_upload_filename(file.filename or "uploaded_policy.txt")
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file extension '{ext}'. Allowed: .pdf, .txt, .md.")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds 5 MB limit.")

    policies_dir = Path("data/policies")
    policies_dir.mkdir(parents=True, exist_ok=True)
    dest_path = policies_dir / safe_name
    dest_path.write_bytes(file_bytes)

    if ext == ".pdf":
        text = load_pdf_text(dest_path)
    else:
        text = file_bytes.decode("utf-8", errors="replace")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Uploaded file contains no readable text.")

    final_doc_id = sanitize_upload_filename(doc_id.strip()) if doc_id and doc_id.strip() else Path(safe_name).stem.lower().replace(" ", "_")
    final_title = title.strip() if title and title.strip() else Path(safe_name).stem.replace("_", " ").title()

    store = get_active_store()
    parents_added, children_added = store.add_document(doc_id=final_doc_id, title=final_title, text=text)

    return PolicyIngestResponse(
        status="success",
        message=f"File '{safe_name}' successfully ingested as '{final_doc_id}'.",
        doc_id=final_doc_id,
        title=final_title,
        parents_added=parents_added,
        children_added=children_added,
        total_parents=len(store.parents),
        total_children=len(store.children),
    )


api_v1_router.include_router(customer_v1_router)
api_v1_router.include_router(admin_v1_router)
