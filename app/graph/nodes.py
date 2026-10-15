import difflib
import logging
import re
from typing import Optional

from app.graph.state import Intent, PolicyDeskState
from app.rag.store import _stem
from app.graph.rag_nodes import get_active_store

logger = logging.getLogger("policydesk.nodes")


def sanitize(state: PolicyDeskState) -> dict:
    """Validate and normalize untrusted user input, resetting per-turn ephemeral state.

    Returns a partial state update with the cleaned string or an error.
    """
    raw_input = state.get("user_input")

    if not isinstance(raw_input, str):
        existing_errors = list(state.get("errors") or [])
        logger.warning("Sanitize rejected non-string input: %r", type(raw_input))
        return {
            "user_input": "",
            "errors": existing_errors + ["User input must be a non-empty string."],
            "answer": None,
            "route": None,
            "retrieved_docs": [],
            "reranked_docs": [],
            "relevance_score": 0.0,
            "is_relevant": False,
        }

    cleaned = raw_input.strip()
    if not cleaned:
        existing_errors = list(state.get("errors") or [])
        logger.warning("Sanitize rejected blank or empty input.")
        return {
            "user_input": "",
            "errors": existing_errors + ["User input cannot be empty or blank."],
            "answer": None,
            "route": None,
            "retrieved_docs": [],
            "reranked_docs": [],
            "relevance_score": 0.0,
            "is_relevant": False,
        }

    logger.info("Sanitized user input (%d chars): %r", len(cleaned), cleaned[:50])
    return {
        "user_input": cleaned,
        "answer": None,
        "route": None,
        "retrieved_docs": [],
        "reranked_docs": [],
        "relevance_score": 0.0,
        "is_relevant": False,
    }


def _deterministic_classify(text: str, dynamic_keywords: Optional[set[str]] = None) -> Intent:
    """Classify input text using deterministic keyword rules and dynamic corpus vocabulary."""
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

    # Dynamic Corpus Vocabulary Check (automatically updated upon document ingestion)
    if dynamic_keywords:
        words = set(re.findall(r"\b[a-zA-Z0-9_]{3,}\b", text_lower))
        stemmed_words = {_stem(w) for w in words}
        if (words | stemmed_words) & dynamic_keywords:
            return Intent.POLICY_QUESTION
        for w in words:
            if len(w) >= 5 and difflib.get_close_matches(w, dynamic_keywords, n=1, cutoff=0.8):
                return Intent.POLICY_QUESTION

    if any(k in text_lower for k in ("account", "balance", "statement", "transaction", "transactions", "charges")):
        return Intent.ACCOUNT_LOOKUP

    return Intent.OUT_OF_SCOPE


def classify_intent(state: PolicyDeskState) -> dict:
    """Classify user intent deterministically with multi-turn and dynamic corpus awareness."""
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

    store = get_active_store()
    dynamic_keywords = store.get_corpus_keywords() if store else set()
    intent = _deterministic_classify(user_input, dynamic_keywords=dynamic_keywords)

    # Check for elliptical follow-up questions referencing recent conversation history
    if intent == Intent.OUT_OF_SCOPE:
        follow_up_prefixes = ("and ", "what if ", "what about ", "why ", "how about ", "if i ", "what happens if", "so what", "do i have to")
        is_follow_up = any(lower.startswith(p) for p in follow_up_prefixes) or (len(lower.split()) < 6 and any(w in lower for w in ("pay", "cost", "fine", "penalty", "leave", "close", "claim", "wait")))
        messages = state.get("messages") or []
        if is_follow_up and messages:
            prior_user_prompts = [m["content"] for m in messages if m.get("role") == "user"]
            if len(prior_user_prompts) >= 2:
                prev_prompt = prior_user_prompts[-2].lower()
                policy_terms = ("deposit", "wire", "transfer", "account", "unclaimed", "dormant", "balance", "fee", "limit", "locker")
                if any(t in prev_prompt for t in policy_terms) or (dynamic_keywords and any(k in prev_prompt for k in dynamic_keywords)):
                    logger.info("[INTENT] Conversational follow-up detected. Preserving POLICY_QUESTION for: %r", user_input[:40])
                    return {"intent": Intent.POLICY_QUESTION.value}

    # Speculative Vector Pre-Flight (< 0.2ms) for queries not captured by keywords
    if intent == Intent.OUT_OF_SCOPE and store:
        preflight_matches = store.retrieve(user_input, top_k=1, expand_window=0)
        if preflight_matches and preflight_matches[0].get("score", 0.0) >= 0.15:
            logger.info("[INTENT] Vector pre-flight match (Score: %.4f) -> POLICY_QUESTION", preflight_matches[0]["score"])
            return {"intent": Intent.POLICY_QUESTION.value}

    logger.info("[INTENT] Classified '%s...' -> %s", user_input[:40], intent.value)
    return {"intent": intent.value}


def out_of_scope_placeholder(state: PolicyDeskState) -> dict:
    """Conversational steering fallback providing helpful guidance and policy discovery."""
    user_input = (state.get("user_input") or "").lower()

    context_clarification = ""
    if any(w in user_input for w in ("pay", "fee", "cost", "charge")):
        context_clarification = (
            "Regarding payments or fees: claiming unclaimed deposits and activating dormant accounts "
            "is completely free under bank policy. However, active checking accounts have standard "
            "minimum balance requirements ($1,500 to waive the $12 monthly fee) and a $5 monthly inactivity fee.\n\n"
        )

    helpful_answer = (
        f"{context_clarification}"
        "I am your PolicyDesk Banking Assistant, and I am here to help answer questions about our policies and services!\n\n"
        "Here are the policy areas I can assist you with:\n"
        "• **Account Maintenance & Balances:** Minimum daily balance requirements ($1,500), monthly fees ($12), and inactivity rules\n"
        "• **Wire Transfers:** Domestic ($25) and international ($45) fees, 4:00 PM EST daily cutoff times, and daily transfer limits\n"
        "• **Deposits & Inoperative Accounts:** Unclaimed deposit timelines (10 years), transfer to DEAF (RBI), and refund claim procedures\n"
        "• **Transaction Disputes:** Filing claims within 60 calendar days and provisional credit timelines\n"
        "• **Secure Account Lookups:** Balance inquiries and recent transactions with two-factor authentication (2FA)\n\n"
        "Please feel free to ask any specific policy question, and I'll find the exact guidelines for you!"
    )
    return {
        "route": "out_of_scope",
        "answer": helpful_answer,
    }
