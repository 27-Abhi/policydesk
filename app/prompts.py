"""Centralized prompts and message templates for PolicyDesk.

Consolidates LLM system prompts, RAG answer templates, 2FA authentication
prompts, human-in-the-loop dispute notifications, and support escalation templates.
"""

# =====================================================================
# 1. LLM System Prompts
# =====================================================================

SYSTEM_GROUNDING_PROMPT = (
    "You are the PolicyDesk Banking Assistant. Answer the user's inquiry accurately "
    "using ONLY the provided bank policy clause context.\n\n"
    "Rules:\n"
    "1. Adhere strictly to the facts, fee amounts, cutoff times, and limits stated in the context.\n"
    "2. If the answer cannot be determined strictly from the provided text, state: "
    "'The provided bank policy does not contain sufficient details to address this inquiry.'\n"
    "3. Do not invent fees, accounts, or policies."
)


# =====================================================================
# 2. RAG & Policy Response Templates
# =====================================================================

POLICY_FALLBACK_ANSWER = "No relevant policy documents found matching your inquiry."

POLICY_ANSWER_PREFIX = "According to bank policy:\n\n{context}"


def format_policy_answer(context: str) -> str:
    """Format a grounded policy answer with the standard compliance prefix."""
    return POLICY_ANSWER_PREFIX.format(context=context)


# =====================================================================
# 3. Two-Factor Authentication (2FA) & Account Lookup Prompts
# =====================================================================

AUTH_PHONE_PROMPT = (
    "For your security and privacy, account balance and transaction lookups require two-factor identity verification. "
    "Please provide your registered 10-digit mobile phone number to begin."
)

AUTH_UNLINKED_ACCOUNT = "Authentication succeeded, but no linked bank account was found."

AUTH_OVERVIEW_TEMPLATE = (
    "Authentication successful! Welcome back.\n\n"
    "Account Overview for {account_type} ({account_number}):\n"
    "  • Current Balance: ${balance:,.2f}\n"
    "  • Pending Holds: ${holds:,.2f}\n"
    "  • Overdraft Limit: ${overdraft_limit:,.2f}\n\n"
    "Would you like to review your recent transactions or make an inquiry?"
)

AUTH_TRANSACTIONS_TEMPLATE = (
    "Recent Transactions for {account_type} ({account_number}):\n\n"
    "{transactions}\n\n"
    "Current Balance: ${balance:,.2f}"
)

AUTH_LOCKOUT_TEMPLATE = "Security Alert: {msg}. Please re-enter your phone number to start a new verification session."

AUTH_OTP_RETRY_TEMPLATE = "{msg} Please try again with the 6-digit code."

AUTH_PHONE_NOT_FOUND_TEMPLATE = (
    "We could not locate an active account registered with {masked}. "
    "Please verify the phone number or contact Customer Care."
)


def format_account_overview(account_type: str, account_number: str, balance: float, holds: float, overdraft_limit: float) -> str:
    return AUTH_OVERVIEW_TEMPLATE.format(
        account_type=account_type,
        account_number=account_number,
        balance=balance,
        holds=holds,
        overdraft_limit=overdraft_limit,
    )


def format_transactions_view(account_type: str, account_number: str, balance: float, transactions: str) -> str:
    return AUTH_TRANSACTIONS_TEMPLATE.format(
        account_type=account_type,
        account_number=account_number,
        balance=balance,
        transactions=transactions,
    )


# =====================================================================
# 4. Human-in-the-Loop (HITL) Dispute Workflow Prompts
# =====================================================================

DISPUTE_INTERRUPT_ACTION = "Compliance approval needed to execute dispute and dispatch funds credit."

DISPUTE_APPROVED_TEMPLATE = (
    "Your dispute for transaction {transaction_id} has been approved by compliance (Case #{dispute_id}). "
    "An automated dispatch has been sent to the backend operations team for processing. "
    "Eligible provisional credit will be credited within 10 business days."
)

DISPUTE_REJECTED_TEMPLATE = (
    "Your dispute request for transaction {transaction_id} (Case #{dispute_id}) has been reviewed and was not approved. "
    "Compliance notes: {comments}. "
    "If you have additional documentation, you may submit an appeal or speak with a representative."
)

DISPUTE_PENDING_ACKNOWLEDGMENT = (
    "Dispute case {dispute_id} for transaction {transaction_id} has been recorded and submitted for compliance review. "
    "Reference thread ID: {thread_id}."
)


def format_dispute_approval(transaction_id: str, dispute_id: str) -> str:
    return DISPUTE_APPROVED_TEMPLATE.format(transaction_id=transaction_id, dispute_id=dispute_id)


def format_dispute_rejection(transaction_id: str, dispute_id: str, comments: str) -> str:
    return DISPUTE_REJECTED_TEMPLATE.format(transaction_id=transaction_id, dispute_id=dispute_id, comments=comments)


def format_dispute_pending(dispute_id: str, transaction_id: str, thread_id: str) -> str:
    return DISPUTE_PENDING_ACKNOWLEDGMENT.format(dispute_id=dispute_id, transaction_id=transaction_id, thread_id=thread_id)


# =====================================================================
# 5. Customer Care Escalation & Ticketing Templates
# =====================================================================

ESCALATION_TICKET_TEMPLATE = (
    "Your request has been transferred to Customer Care.\n\n"
    "Support Ticket Details:\n"
    "  • Ticket Number: #{ticket_id}\n"
    "  • Department: Customer Care\n"
    "  • Priority: {priority}\n"
    "  • Linked Account: {account_number}\n"
    "  • Estimated Wait Time: {wait_time}\n\n"
    "A Customer Care specialist has received your case details and will assist you shortly. "
    "Please keep your ticket number handy for reference."
)


def format_escalation_ticket(ticket_id: str, priority: str, account_number: str, wait_time: str) -> str:
    return ESCALATION_TICKET_TEMPLATE.format(
        ticket_id=ticket_id,
        priority=priority.replace("_", " "),
        account_number=account_number,
        wait_time=wait_time,
    )


# =====================================================================
# 6. Fallback & Safe Refusal Prompts
# =====================================================================

OUT_OF_SCOPE_REFUSAL = (
    "I apologize, but your inquiry falls outside supported banking operations. "
    "I can assist with bank policies (wires, fees, deposits), secure account lookups, "
    "transaction disputes, or transfer you to Customer Care."
)
