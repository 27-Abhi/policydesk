from enum import Enum
from typing import Optional
from typing_extensions import TypedDict


class Intent(str, Enum):
    """Constrained intent classifications allowed by PolicyDesk."""
    POLICY_QUESTION = "policy_question"
    ACCOUNT_LOOKUP = "account_lookup"
    DISPUTE_ACTION = "dispute_action"
    HUMAN_REQUEST = "human_request"
    OUT_OF_SCOPE = "out_of_scope"


class PolicyDeskState(TypedDict, total=False):
    """Typed state flowing through the PolicyDesk graph.

    Fields:
        user_input: Cleaned input string provided by the user.
        intent: Classified user intent, or None prior to classification.
        route: Name of the route/placeholder node reached.
        errors: List of validation or processing error messages encountered.
        retrieved_docs: Parent policy chunks retrieved from vector store.
        reranked_docs: Scored and reranked parent policy chunks.
        relevance_score: Graded relevance score for retrieved chunks.
        is_relevant: Boolean indicating if retrieved chunks meet relevance threshold.
        answer: Synthesized response to the policy query.
    """
    user_input: str
    intent: Optional[Intent]
    route: Optional[str]
    errors: list[str]
    retrieved_docs: list[str]
    reranked_docs: list[str]
    relevance_score: float
    is_relevant: bool
    answer: Optional[str]

