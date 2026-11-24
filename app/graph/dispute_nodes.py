import logging
import re
import uuid
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.graph.state import PolicyDeskState
from app.observability import trace_node
from app.prompts import (
    DISPUTE_INTERRUPT_ACTION,
    format_dispute_approval,
    format_dispute_rejection,
)
from app.tools.bank_db import get_bank_db

logger = logging.getLogger("policydesk.dispute")


def extract_dispute_details(state: PolicyDeskState, config: Optional[RunnableConfig] = None) -> dict:
    with trace_node("extract_dispute_details", state) as metrics:
        user_input = state.get("user_input", "")

        tx_match = re.search(r"\b(?:txn|trx|tx)[-_]?[0-9a-zA-Z]+\b", user_input, re.IGNORECASE)
        transaction_id = tx_match.group(0).upper() if tx_match else "TX-GENERAL"
        dispute_id = f"DISP-{uuid.uuid4().hex[:8].upper()}"

        thread_id = "unknown"
        if isinstance(config, dict):
            thread_id = str(config.get("configurable", {}).get("thread_id", "unknown"))

        get_bank_db().record_dispute(
            dispute_id=dispute_id,
            thread_id=thread_id,
            transaction_id=transaction_id,
            status="PENDING_HUMAN_REVIEW",
            comments=user_input[:200],
        )

        logger.info("[DISPUTE:EXTRACT] Created dispute case %s for transaction %s", dispute_id, transaction_id)
        return {
            "dispute_id": dispute_id,
            "transaction_id": transaction_id,
            "dispute_status": "pending_human_review",
            "route": "dispute_action",
            "metrics": metrics,
        }


def require_human_approval(state: PolicyDeskState) -> dict:
    dispute_id = state.get("dispute_id")
    transaction_id = state.get("transaction_id")
    user_input = state.get("user_input", "")

    logger.info("[DISPUTE:INTERRUPT] Pausing execution for human review on case %s (%s)", dispute_id, transaction_id)

    human_decision: dict[str, Any] = interrupt(
        {
            "dispute_id": dispute_id,
            "transaction_id": transaction_id,
            "user_inquiry": user_input,
            "action_required": DISPUTE_INTERRUPT_ACTION,
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


def finalize_dispute(state: PolicyDeskState, config: Optional[RunnableConfig] = None) -> dict:
    with trace_node("finalize_dispute", state) as metrics:
        dispute_id = state.get("dispute_id", "UNKNOWN")
        transaction_id = state.get("transaction_id", "UNKNOWN")
        status = state.get("dispute_status", "pending_human_review")
        comments = state.get("dispute_comments") or "No comments provided."

        thread_id = "unknown"
        if isinstance(config, dict):
            thread_id = str(config.get("configurable", {}).get("thread_id", "unknown"))

        if status == "approved":
            logger.info("[DISPUTE:DISPATCH] Automated dispatch email sent to disputes-ops@bank.internal for case %s", dispute_id)
            answer = format_dispute_approval(transaction_id, dispute_id)
            notification_sent = True
            db_status = "APPROVED"
        else:
            logger.info("[DISPUTE:REJECTED] Case %s marked as rejected. Notifying customer.", dispute_id)
            answer = format_dispute_rejection(transaction_id, dispute_id, comments)
            notification_sent = False
            db_status = "REJECTED"

        get_bank_db().record_dispute(
            dispute_id=dispute_id,
            thread_id=thread_id,
            transaction_id=transaction_id,
            status=db_status,
            comments=comments,
        )

        return {
            "answer": answer,
            "route": "dispute_action",
            "dispute_notification_sent": notification_sent,
            "metrics": metrics,
        }
