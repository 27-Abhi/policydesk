from enum import Enum
from typing import Any, Optional
from typing_extensions import TypedDict


class Intent(str, Enum):
    """Constrained intent classifications allowed by PolicyDesk."""

    POLICY_QUESTION = "policy_question"
    ACCOUNT_LOOKUP = "account_lookup"
    DISPUTE_ACTION = "dispute_action"
    HUMAN_REQUEST = "human_request"
    OUT_OF_SCOPE = "out_of_scope"


class PolicyDeskState(TypedDict, total=False):
    """Typed state flowing through the PolicyDesk agentic workflow graph."""

    user_input: str
    intent: Optional[Intent]
    route: Optional[str]
    errors: list[str]
    injection_detected: bool
    retrieved_docs: list[str]
    reranked_docs: list[str]
    citations: list[dict[str, Any]]
    relevance_score: float
    is_relevant: bool
    retrieval_attempts: int
    generation_attempts: int
    rewritten_query: Optional[str]
    grounded: bool
    grounding_reason: Optional[str]
    defer_generation: bool
    answer: Optional[str]
    dispute_id: Optional[str]
    transaction_id: Optional[str]
    dispute_status: Optional[str]
    dispute_comments: Optional[str]
    dispute_notification_sent: Optional[bool]
    auth_step: Optional[str]
    auth_phone: Optional[str]
    authenticated: Optional[bool]
    account_number: Optional[str]
    escalation_ticket_id: Optional[str]
    escalation_priority: Optional[str]
    messages: list[dict[str, str]]
    conversation_history: list[dict[str, str]]
    contextual_query: Optional[str]
    metrics: dict[str, Any]
