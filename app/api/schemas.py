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


class ChatDebugResponse(BaseModel):
    user_input: str
    intent: Optional[str] = None
    route: Optional[str] = None
    is_relevant: Optional[bool] = None
    relevance_score: Optional[float] = None
    retrieval_attempts: int = 0
    generation_attempts: int = 0
    rewritten_query: Optional[str] = None
    grounded: Optional[bool] = None
    retrieved_docs: list[str] = Field(default_factory=list)
    reranked_docs: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    answer: Optional[str] = None
    errors: list[str] = Field(default_factory=list)


class DisputeReviewRequest(BaseModel):
    approved: bool = Field(..., description="Whether compliance approves the dispute")
    comments: Optional[str] = Field(default="", description="Review comments or rejection reason")


class DisputeReviewResponse(BaseModel):
    thread_id: str
    dispute_id: str
    transaction_id: str
    dispute_status: str
    dispute_comments: Optional[str] = None
    dispute_notification_sent: bool
    answer: str


class PolicyIngestRequest(BaseModel):
    doc_id: str = Field(..., min_length=1, description="Unique identifier for the document")
    title: str = Field(..., min_length=1, description="Human-readable title of the policy")
    text: str = Field(..., min_length=1, description="Full policy text with paragraphs")


class PolicyIngestResponse(BaseModel):
    status: str = "success"
    message: str
    doc_id: str
    title: str
    parents_added: int
    children_added: int
    total_parents: int
    total_children: int


class PolicyDocumentSummary(BaseModel):
    doc_id: str
    title: str
    parent_chunks: int


class PolicyListResponse(BaseModel):
    total_documents: int
    total_parents: int
    total_children: int
    policies: list[PolicyDocumentSummary]
