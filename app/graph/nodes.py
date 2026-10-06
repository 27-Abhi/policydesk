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

    if "policy" in text_lower or "rules" in text_lower:
        return Intent.POLICY_QUESTION

    if any(k in text_lower for k in ("dispute", "chargeback", "fraud")):
        return Intent.DISPUTE_ACTION

    if any(k in text_lower for k in ("account", "balance", "statement")):
        return Intent.ACCOUNT_LOOKUP

    if any(k in text_lower for k in ("human", "agent", "representative", "speak to", "support person")):
        return Intent.HUMAN_REQUEST

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
