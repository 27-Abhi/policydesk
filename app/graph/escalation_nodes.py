import logging
import time
import uuid

from app.graph.state import PolicyDeskState

logger = logging.getLogger("policydesk.escalation")

_ESCALATION_TICKETS: list[dict] = []


def get_escalation_tickets() -> list[dict]:
    """Return all created support escalation tickets."""
    return list(_ESCALATION_TICKETS)


def resolve_escalation_ticket(ticket_id: str) -> bool:
    """Mark an escalation ticket as resolved."""
    for t in _ESCALATION_TICKETS:
        if t["ticket_id"] == ticket_id:
            t["status"] = "Resolved"
            return True
    return False


def handle_human_escalation(state: PolicyDeskState) -> dict:
    """Escalate customer inquiry to Customer Care with prioritized ticketing."""
    user_input = state.get("user_input", "")
    account_number = state.get("account_number") or "Unauthenticated"
    phone = state.get("auth_phone") or "Not provided"

    input_lower = user_input.lower()

    # Determine priority based on risk and urgency keywords
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
    _ESCALATION_TICKETS.append(ticket_record)

    logger.info(
        "[ESCALATION:TICKET] Created ticket %s (Priority: %s) for account %s [Phone: %s]: %r",
        ticket_id,
        priority,
        account_number,
        phone,
        query_summary,
    )

    answer = (
        f"Your request has been transferred to Customer Care.\n\n"
        f"Support Ticket Details:\n"
        f"  • Ticket Number: #{ticket_id}\n"
        f"  • Department: Customer Care\n"
        f"  • Priority: {priority.replace('_', ' ')}\n"
        f"  • Linked Account: {account_number}\n"
        f"  • Estimated Wait Time: {wait_time}\n\n"
        f"A Customer Care specialist has received your case details and will assist you shortly. "
        f"Please keep your ticket number handy for reference."
    )

    return {
        "escalation_ticket_id": ticket_id,
        "escalation_priority": priority,
        "answer": answer,
        "route": "human_request",
    }
