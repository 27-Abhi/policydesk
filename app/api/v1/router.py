import asyncio
import json
import time
import uuid
from typing import AsyncGenerator, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.graph.graph import get_graph
from app.prompts import format_dispute_pending
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

    final_state = graph.invoke(
        {"user_input": prompt},
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
        answer = final_state.get("answer") or f"Routed to: {final_state.get('route', 'out_of_scope')}"

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

    final_state = graph.invoke(
        {"user_input": last_user_prompt},
        config={"configurable": {"thread_id": thread_id}},
    )

    if final_state.get("__interrupt__") or final_state.get("dispute_status") == "pending_human_review":
        dispute_id = final_state.get("dispute_id", "PENDING")
        tx_id = final_state.get("transaction_id", "GENERAL")
        answer = format_dispute_pending(dispute_id=dispute_id, transaction_id=tx_id, thread_id=thread_id)
    else:
        answer = final_state.get("answer") or f"Routed to: {final_state.get('route', 'out_of_scope')}"

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

