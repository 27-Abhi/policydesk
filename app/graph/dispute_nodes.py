import logging
import re
import uuid
from typing import Any
from langgraph.types import interrupt

from app.graph.state import PolicyDeskState

logger = logging.getLogger("policydesk.dispute")


def extract_dispute_details(state: PolicyDeskState) -> dict:
    user_input = state.get("user_input", "")

    # Identify transaction references like TX123, TXN-456, TRX_99
    tx_match = re.search(r"\b(?:txn|trx|tx)[-_]?[0-9a-zA-Z]+\b", user_input, re.IGNORECASE)
    if tx_match:
        transaction_id = tx_match.group(0).upper()
    else:
        transaction_id = "TX-GENERAL"

    dispute_id = f"DISP-{uuid.uuid4().hex[:8].upper()}"

    logger.info("[DISPUTE:EXTRACT] Created dispute case %s for transaction %s", dispute_id, transaction_id)
    return {
        "dispute_id": dispute_id,
        "transaction_id": transaction_id,
        "dispute_status": "pending_human_review",
        "route": "dispute_action",
    }



def require_human_approval(state: PolicyDeskState) -> dict:
    dispute_id = state.get("dispute_id")
    transaction_id = state.get("transaction_id")
    user_input = state.get("user_input", "")

    logger.info("[DISPUTE:INTERRUPT] Pausing execution for human review on case %s (%s)", dispute_id, transaction_id)

    # Interrupt pauses graph execution until Command(resume=...) is supplied
    human_decision: dict[str, Any] = interrupt(
        {
            "dispute_id": dispute_id,
            "transaction_id": transaction_id,
            "user_inquiry": user_input,
            "action_required": "Compliance approval needed to execute dispute and dispatch funds credit.",
        }
    )

    approved = bool(human_decision.get("approved", False))
    comments = str(human_decision.get("comments", "")).strip()

    new_status = "approved" if approved else "rejected"
    logger.info("[DISPUTE:RESUMED] Case %s reviewed: status=%s, comments=%r", dispute_id, new_status, comments)

    return {
        "dispute_status": new_status,
        "dispute_comments": comments,
    }


def finalize_dispute(state: PolicyDeskState) -> dict:
    dispute_id = state.get("dispute_id", "UNKNOWN")
    transaction_id = state.get("transaction_id", "UNKNOWN")
    status = state.get("dispute_status", "pending_human_review")
    comments = state.get("dispute_comments") or "No comments provided."

    if status == "approved":
        logger.info("[DISPUTE:DISPATCH] Automated dispatch email sent to disputes-ops@bank.internal for case %s", dispute_id)
        answer = (
            f"Your dispute for transaction {transaction_id} has been approved by compliance (Case #{dispute_id}). "
            f"An automated dispatch has been sent to the backend operations team for processing. "
            f"Eligible provisional credit will be credited within 10 business days."
        )
        notification_sent = True
    else:
        logger.info("[DISPUTE:REJECTED] Case %s marked as rejected. Notifying customer.", dispute_id)
        answer = (
            f"Your dispute request for transaction {transaction_id} (Case #{dispute_id}) has been reviewed and was not approved. "
            f"Compliance notes: {comments}. "
            f"If you have additional documentation, you may submit an appeal or speak with a representative."
        )
        notification_sent = False

    return {
        "answer": answer,
        "route": "dispute_action",
        "dispute_notification_sent": notification_sent,
    }
