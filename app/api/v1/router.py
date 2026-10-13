import time
import uuid
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph

from app.api.deps import get_graph
from app.api.schemas import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    DeltaMessage,
)

api_v1_router = APIRouter(tags=["Chat Completions"])


async def _stream_chat_completion(
    request: ChatCompletionRequest,
    graph: CompiledStateGraph,
    prompt: str,
) -> AsyncGenerator[str, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    final_state = graph.invoke({"user_input": prompt})
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
    graph: CompiledStateGraph = Depends(get_graph),
):
    user_prompts = [m.content.strip() for m in request.messages if m.role == "user" and m.content.strip()]
    if not user_prompts:
        raise HTTPException(
            status_code=400,
            detail="Request must contain at least one non-empty user message.",
        )

    last_user_prompt = user_prompts[-1]

    if request.stream:
        return StreamingResponse(
            _stream_chat_completion(request, graph, last_user_prompt),
            media_type="text/event-stream",
        )

    final_state = graph.invoke({"user_input": last_user_prompt})
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
