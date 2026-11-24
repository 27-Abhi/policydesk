import logging
import threading
import time
import uuid
from typing import Optional

from langchain_core.runnables import RunnableConfig
from app.graph.state import PolicyDeskState
from app.observability import trace_node
from app.prompts import format_escalation_ticket
from app.tools.bank_db import get_bank_db

logger = logging.getLogger("policydesk.escalation")

_ESCALATION_LOCK = threading.Lock()
_ESCALATION_TICKETS: list[dict] = []


def get_escalation_tickets() -> list[dict]:
    """Return all created support escalation tickets."""
    with _ESCALATION_LOCK:
        return [dict(t) for t in _ESCALATION_TICKETS]


def resolve_escalation_ticket(ticket_id: str) -> bool:
    """Mark an escalation ticket as resolved in memory and database."""
    resolved = False
    with _ESCALATION_LOCK:
        for t in _ESCALATION_TICKETS:
            if t["ticket_id"] == ticket_id:
                t["status"] = "Resolved"
                resolved = True
                break
    if resolved:
        get_bank_db().resolve_escalation(ticket_id)
    return resolved


def handle_human_escalation(state: PolicyDeskState, config: Optional[RunnableConfig] = None) -> dict:
    """Escalate customer inquiry to Customer Care with prioritized ticketing."""
    with trace_node("handle_human_escalation", state) as metrics:
        user_input = state.get("user_input", "")
        account_number = state.get("account_number") or "Unauthenticated"
        phone = state.get("auth_phone") or "Not provided"

        thread_id = "unknown"
        if isinstance(config, dict):
            thread_id = str(config.get("configurable", {}).get("thread_id", "unknown"))

        input_lower = user_input.lower()
        if any(k in input_lower for k in ("fraud", "unauthorized", "stolen", "locked out", "compromised", "identity theft")):
            priority = "P1_CRITICAL"
            wait_time = "< 3 minutes"
        elif any(k in input_lower for k in ("dispute", "hold", "overdraft", "wire", "urgent", "emergency", "penalty")):
            priority = "P2_HIGH"
            wait_time = "< 10 minutes"
        else:
            priority = "P3_STANDARD"
            wait_time = "< 20 minutes"

        date_str = time.strftime("%Y%m%d")
        ticket_id = f"TICK-{date_str}-{uuid.uuid4().hex[:6].upper()}"
        query_summary = user_input[:80] + ("..." if len(user_input) > 80 else "")

        ticket_record = {
            "ticket_id": ticket_id,
            "priority": priority,
            "account_number": account_number,
            "phone": phone,
            "query_summary": query_summary,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Open",
            "wait_time": wait_time,
        }
        with _ESCALATION_LOCK:
            _ESCALATION_TICKETS.append(ticket_record)

        get_bank_db().record_escalation(
            ticket_id=ticket_id,
            thread_id=thread_id,
            priority=priority,
            reason=query_summary,
        )

        logger.info(
            "[ESCALATION:TICKET] Created ticket %s (Priority: %s) for account %s: %r",
            ticket_id,
            priority,
            account_number,
            query_summary,
        )

        answer = format_escalation_ticket(
            ticket_id=ticket_id,
            priority=priority,
            account_number=account_number,
            wait_time=wait_time,
        )

        return {
            "escalation_ticket_id": ticket_id,
            "escalation_priority": priority,
            "answer": answer,
            "route": "human_request",
            "metrics": metrics,
        }
