import logging

from app.graph.state import Intent, PolicyDeskState

logger = logging.getLogger("policydesk.nodes")


def sanitize(state: PolicyDeskState) -> dict:
    """Validate and normalize untrusted user input.

    Returns a partial state update with the cleaned string or an error.
    """
    raw_input = state.get("user_input")

    if not isinstance(raw_input, str):
        existing_errors = list(state.get("errors") or [])
        logger.warning("Sanitize rejected non-string input: %r", type(raw_input))
        return {
            "user_input": "",
            "errors": existing_errors + ["User input must be a non-empty string."],
        }

    cleaned = raw_input.strip()
    if not cleaned:
        existing_errors = list(state.get("errors") or [])
        logger.warning("Sanitize rejected blank or empty input.")
        return {
            "user_input": "",
            "errors": existing_errors + ["User input cannot be empty or blank."],
        }

    logger.info("Sanitized user input (%d chars): %r", len(cleaned), cleaned[:50])
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

    if any(k in text_lower for k in ("account", "balance", "statement", "transaction", "transactions", "charges")):
        return Intent.ACCOUNT_LOOKUP

    return Intent.OUT_OF_SCOPE


def classify_intent(state: PolicyDeskState) -> dict:
    """Classify user intent deterministically.

    Fails safe to OUT_OF_SCOPE if errors exist or text is unrecognized.
    """
    if state.get("errors"):
        logger.warning("[INTENT] State has errors. Failing safe to OUT_OF_SCOPE.")
        return {"intent": Intent.OUT_OF_SCOPE.value}

    auth_step = state.get("auth_step")
    authenticated = bool(state.get("authenticated", False))
    user_input = state.get("user_input", "")
    lower = user_input.lower()

    # Preserve account lookup flow during multi-turn 2FA unless user asks to cancel/escalate
    if auth_step in ("awaiting_phone", "awaiting_otp"):
        if not any(w in lower for w in ("cancel", "stop", "never mind", "agent", "human", "representative")):
            logger.info("[INTENT] Preserving ACCOUNT_LOOKUP for active 2FA step (%s)", auth_step)
            return {"intent": Intent.ACCOUNT_LOOKUP.value}

    # In an authenticated session, keep inquiries about transactions/balance in account lookup
    if authenticated and any(w in lower for w in ("transaction", "transactions", "balance", "account", "statement", "charges", "recent")):
        if not any(w in lower for w in ("dispute", "fraud", "unauthorized", "agent", "human", "representative")):
            logger.info("[INTENT] Retaining ACCOUNT_LOOKUP for authenticated customer query")
            return {"intent": Intent.ACCOUNT_LOOKUP.value}

    intent = _deterministic_classify(user_input)
    logger.info("[INTENT] Classified '%s...' -> %s", user_input[:40], intent.value)
    return {"intent": intent.value}


def out_of_scope_placeholder(state: PolicyDeskState) -> dict:
    """Terminal placeholder node for out-of-scope / fallback requests."""
    return {"route": "out_of_scope"}
