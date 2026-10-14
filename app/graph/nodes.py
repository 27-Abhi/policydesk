"""Graph nodes for PolicyDesk workflow."""

from app.graph.state import Intent, PolicyDeskState


def sanitize(state: PolicyDeskState) -> dict:
    """Validate and normalize untrusted user input.

    Returns a partial state update with the cleaned string or an error.
    """
    raw_input = state.get("user_input")

    if not isinstance(raw_input, str):
        existing_errors = list(state.get("errors") or [])
        return {
            "user_input": "",
            "errors": existing_errors + ["User input must be a non-empty string."],
        }

    cleaned = raw_input.strip()
    if not cleaned:
        existing_errors = list(state.get("errors") or [])
        return {
            "user_input": "",
            "errors": existing_errors + ["User input cannot be empty or blank."],
        }

    return {"user_input": cleaned}


def _deterministic_classify(text: str) -> Intent:
    """Classify input text using deterministic keyword rules."""
    text_lower = text.lower()

    if any(k in text_lower for k in ("human", "agent", "representative", "speak to", "support person", "operator")):
        return Intent.HUMAN_REQUEST

    # Inquiries explicitly referencing policy or rules route to policy question
    if "policy" in text_lower or "rules" in text_lower or "guidelines" in text_lower:
        return Intent.POLICY_QUESTION

    if any(k in text_lower for k in ("dispute", "chargeback", "fraud", "unauthorized")):
        return Intent.DISPUTE_ACTION

    if any(k in text_lower for k in ("account balance", "my balance", "account statement", "my account", "transaction history")):
        return Intent.ACCOUNT_LOOKUP

    policy_keywords = (
        "fee", "fees", "cost", "cutoff", "cut-off", "limit", "limits",
        "wire", "transfer", "wires", "transfers", "deposit", "deposits",
        "withdrawal", "withdrawals", "hold", "holds", "overdraft",
        "penalty", "requirement", "requirements", "minimum", "eligibility",
        "terms", "interest", "apy", "fdic", "dormant", "minor", "nomination",
        "nominee", "inoperative", "unclaimed", "savings account", "checking account"
    )
    if any(k in text_lower for k in policy_keywords):
        return Intent.POLICY_QUESTION

    if any(k in text_lower for k in ("account", "balance", "statement")):
        return Intent.ACCOUNT_LOOKUP

    return Intent.OUT_OF_SCOPE


def classify_intent(state: PolicyDeskState) -> dict:
    """Classify user intent deterministically.

    Fails safe to OUT_OF_SCOPE if errors exist or text is unrecognized.
    """
    if state.get("errors"):
        return {"intent": Intent.OUT_OF_SCOPE}

    user_input = state.get("user_input", "")
    intent = _deterministic_classify(user_input)
    return {"intent": intent}


def policy_placeholder(state: PolicyDeskState) -> dict:
    """Terminal placeholder node for policy inquiries."""
    return {"route": "policy_question"}


def account_placeholder(state: PolicyDeskState) -> dict:
    """Terminal placeholder node for account inquiries."""
    return {"route": "account_lookup"}


def dispute_placeholder(state: PolicyDeskState) -> dict:
    """Terminal placeholder node for dispute actions."""
    return {"route": "dispute_action"}


def human_placeholder(state: PolicyDeskState) -> dict:
    """Terminal placeholder node for human escalation requests."""
    return {"route": "human_request"}


def out_of_scope_placeholder(state: PolicyDeskState) -> dict:
    """Terminal placeholder node for out-of-scope / fallback requests."""
    return {"route": "out_of_scope"}
