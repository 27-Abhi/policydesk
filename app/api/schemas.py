import time
import uuid
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "policydesk"
    messages: list[ChatMessage] = Field(..., min_length=1)
    stream: bool = False

    @field_validator("messages")
    @classmethod
    def validate_user_message_present(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        user_messages = [m for m in messages if m.role == "user" and m.content.strip()]
        if not user_messages:
            raise ValueError("Request must include at least one user message with non-empty content.")
        return messages


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Optional[str] = "stop"


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "policydesk"
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage = Field(default_factory=ChatCompletionUsage)


class DeltaMessage(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class ChatCompletionChunkChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str = "policydesk"
    choices: list[ChatCompletionChunkChoice]
