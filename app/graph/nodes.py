import difflib
import logging
import os
import re
from typing import Optional

from app.graph.rag_nodes import get_active_store
from app.graph.state import Intent, PolicyDeskState
from app.observability import new_request_id, trace_node
from app.prompts import INTENT_CLASSIFICATION_PROMPT, PROMPT_INJECTION_REFUSAL
from app.rag.store import _has_non_deposit_product_mismatch, _stem

logger = logging.getLogger("policydesk.nodes")

MAX_INPUT_LENGTH = 2000

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+|previous\s+|prior\s+|above\s+)*instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+|previous\s+|prior\s+)*instructions", re.IGNORECASE),
    re.compile(r"(?:reveal|show|print|dump|output)\s+(?:your\s+)?(?:system|hidden|developer)\s+prompt", re.IGNORECASE),
    re.compile(r"(?:bypass|skip|disable|override)\s+(?:2fa|otp|verification|compliance|approval|human\s+review|guardrails)", re.IGNORECASE),
    re.compile(r"(?:auto[- ]?approve|self[- ]?approve)\s+(?:my\s+)?dispute", re.IGNORECASE),
    re.compile(r"<\|(?:im_start|system|endoftext)\|>", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*###\s*(?:system|instruction)\b", re.IGNORECASE),
)

_NON_BANKING_OUT_OF_SCOPE_PATTERNS: tuple[str, ...] = (
    "tell me a joke",
    "write me a python",
    "python game",
    "write a python",
    "recipe for",
    "chocolate cake",
    "weather today",
    "stock market index",
)


def _detect_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def sanitize(state: PolicyDeskState) -> dict:
    """Validate, sanitize, and guard untrusted user input while resetting per-turn ephemeral state."""
    with trace_node("sanitize", state) as metrics:
        metrics["request_id"] = new_request_id()
        raw_input = state.get("user_input")

        ephemeral_reset = {
            "answer": None,
            "route": None,
            "retrieved_docs": [],
            "reranked_docs": [],
            "citations": [],
            "relevance_score": 0.0,
            "is_relevant": False,
            "retrieval_attempts": 0,
            "generation_attempts": 0,
            "rewritten_query": None,
            "grounded": False,
            "grounding_reason": None,
            "defer_generation": bool(state.get("defer_generation", False)),
            "contextual_query": None,
            "injection_detected": False,
            "metrics": metrics,
        }

        if not isinstance(raw_input, str):
            existing_errors = list(state.get("errors") or [])
            logger.warning("Sanitize rejected non-string input: %r", type(raw_input))
            return {
                **ephemeral_reset,
                "user_input": "",
                "intent": Intent.OUT_OF_SCOPE.value,
                "errors": existing_errors + ["User input must be a non-empty string."],
            }

        cleaned = _CONTROL_CHARS_RE.sub("", raw_input).strip()
        if not cleaned:
            existing_errors = list(state.get("errors") or [])
            logger.warning("Sanitize rejected blank or empty input.")
            return {
                **ephemeral_reset,
                "user_input": "",
                "intent": Intent.OUT_OF_SCOPE.value,
                "errors": existing_errors + ["User input cannot be empty or blank."],
            }

        if len(cleaned) > MAX_INPUT_LENGTH:
            existing_errors = list(state.get("errors") or [])
            logger.warning("Sanitize rejected oversized input (%d chars).", len(cleaned))
            return {
                **ephemeral_reset,
                "user_input": cleaned[:MAX_INPUT_LENGTH],
                "intent": Intent.OUT_OF_SCOPE.value,
                "errors": existing_errors + [f"User input exceeds maximum allowed length of {MAX_INPUT_LENGTH} characters."],
            }

        if _detect_prompt_injection(cleaned):
            existing_errors = list(state.get("errors") or [])
            logger.warning("[SECURITY:INJECTION] Blocked suspected prompt-injection attempt: %r", cleaned[:80])
            return {
                **ephemeral_reset,
                "user_input": cleaned,
                "intent": Intent.OUT_OF_SCOPE.value,
                "injection_detected": True,
                "errors": existing_errors + ["Input blocked due to unsafe instruction override attempt."],
            }

        # Accumulate bounded multi-turn user history inside checkpointed state
        history = list(state.get("conversation_history") or [])
        if not history or history[-1].get("content") != cleaned or history[-1].get("role") != "user":
            history.append({"role": "user", "content": cleaned})
        history = history[-20:]

        logger.info("Sanitized user input (%d chars): %r", len(cleaned), cleaned[:50])
        update: dict = {
            **ephemeral_reset,
            "user_input": cleaned,
            "conversation_history": history,
        }
        # Clear any errors from an earlier completed turn while leaving error-free single turns clean
        if state.get("errors"):
            update["errors"] = []
        return update


def _deterministic_classify(
    text: str,
    dynamic_keywords: Optional[set[str]] = None,
    store: Optional[object] = None,
) -> Intent:
    """Classify input text using deterministic safety guards, domain rules, and corpus vocabulary."""
    text_lower = text.lower()

    if any(k in text_lower for k in ("human", "agent", "representative", "speak to", "support person", "operator")):
        return Intent.HUMAN_REQUEST

    is_oos_product = (
        store.is_out_of_domain_query(text)
        if store is not None and hasattr(store, "is_out_of_domain_query")
        else _has_non_deposit_product_mismatch(text_lower)
    )
    if any(p in text_lower for p in _NON_BANKING_OUT_OF_SCOPE_PATTERNS) or is_oos_product:
        return Intent.OUT_OF_SCOPE

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
        "terms", "interest", "apy", "fdic", "dicgc", "dormant", "minor", "nomination",
        "nominee", "inoperative", "unclaimed", "savings account", "checking account",
        "locker", "kyc", "tds", "fcnr", "bsbda", "deaf", "udgam", "maturity",
    )
    if any(k in text_lower for k in policy_keywords):
        return Intent.POLICY_QUESTION

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


def _optional_llm_classify(user_input: str) -> Optional[Intent]:
    """Optionally use live LLM structured JSON classification when USE_LLM_ROUTER=true."""
    if os.getenv("USE_LLM_ROUTER", "false").strip().lower() not in ("1", "true", "yes"):
        return None
    try:
        from app.llm.client import DeterministicMockClient, get_llm_client

        client = get_llm_client()
        if isinstance(client, DeterministicMockClient):
            return None
        parsed = client.complete_json(INTENT_CLASSIFICATION_PROMPT, user_input, max_tokens=80)
        if not parsed or "intent" not in parsed:
            return None
        candidate = str(parsed["intent"]).strip().lower()
        return Intent(candidate)
    except Exception as exc:
        logger.debug("[INTENT:LLM] Structured classification skipped: %s", exc)
        return None


def classify_intent(state: PolicyDeskState) -> dict:
    """Classify user intent with safety checks, optional structured LLM routing, and multi-turn memory."""
    with trace_node("classify_intent", state) as metrics:
        if state.get("injection_detected") or state.get("errors"):
            logger.warning("[INTENT] State flagged errors/injection. Failing safe to OUT_OF_SCOPE.")
            return {"intent": Intent.OUT_OF_SCOPE.value, "metrics": metrics}

        auth_step = state.get("auth_step")
        authenticated = bool(state.get("authenticated", False))
        user_input = state.get("user_input", "")
        lower = user_input.lower()

        if auth_step in ("awaiting_phone", "awaiting_otp"):
            if not any(w in lower for w in ("cancel", "stop", "never mind", "agent", "human", "representative", "dispute")):
                logger.info("[INTENT] Preserving ACCOUNT_LOOKUP for active 2FA step (%s)", auth_step)
                return {"intent": Intent.ACCOUNT_LOOKUP.value, "metrics": metrics}

        if authenticated and any(w in lower for w in ("transaction", "transactions", "balance", "account", "statement", "charges", "recent")):
            if not any(w in lower for w in ("dispute", "fraud", "unauthorized", "agent", "human", "representative", "policy", "rules")):
                logger.info("[INTENT] Retaining ACCOUNT_LOOKUP for authenticated customer query")
                return {"intent": Intent.ACCOUNT_LOOKUP.value, "metrics": metrics}

        store = get_active_store()
        dynamic_keywords = store.get_corpus_keywords() if store else set()
        intent = _deterministic_classify(user_input, dynamic_keywords=dynamic_keywords, store=store)
        is_oos_product = store.is_out_of_domain_query(user_input) if store else _has_non_deposit_product_mismatch(lower)

        # Allow optional structured LLM router to disambiguate ambiguous or out-of-scope candidates
        if intent == Intent.OUT_OF_SCOPE and not is_oos_product:
            llm_intent = _optional_llm_classify(user_input)
            if llm_intent is not None:
                intent = llm_intent

        # Multi-turn follow-up resolution using passed messages OR checkpointed conversation_history
        if intent == Intent.OUT_OF_SCOPE and not is_oos_product:
            follow_up_prefixes = (
                "and ", "what if ", "what about ", "why ", "how about ",
                "if i ", "what happens if", "so what", "do i have to",
            )
            is_follow_up = any(lower.startswith(p) for p in follow_up_prefixes) or (
                len(lower.split()) < 6 and any(w in lower for w in ("pay", "cost", "fine", "penalty", "leave", "close", "claim", "wait"))
            )
            messages = state.get("messages") or state.get("conversation_history") or []
            if is_follow_up and messages:
                prior_user_prompts = [m["content"] for m in messages if m.get("role") == "user"]
                if len(prior_user_prompts) >= 2:
                    prev_prompt = prior_user_prompts[-2].lower()
                    policy_terms = ("deposit", "wire", "transfer", "account", "unclaimed", "dormant", "balance", "fee", "limit", "locker")
                    if any(t in prev_prompt for t in policy_terms) or (dynamic_keywords and any(k in prev_prompt for k in dynamic_keywords)):
                        logger.info("[INTENT] Conversational follow-up detected. Preserving POLICY_QUESTION for: %r", user_input[:40])
                        return {"intent": Intent.POLICY_QUESTION.value, "metrics": metrics}

        # Speculative hybrid vector pre-flight for paraphrased policy inquiries
        if intent == Intent.OUT_OF_SCOPE and store and not is_oos_product and not any(p in lower for p in _NON_BANKING_OUT_OF_SCOPE_PATTERNS):
            preflight_matches = store.retrieve(user_input, top_k=1, expand_window=0)
            if preflight_matches and preflight_matches[0].get("score", 0.0) >= 0.15:
                logger.info("[INTENT] Vector pre-flight match (Score: %.4f) -> POLICY_QUESTION", preflight_matches[0]["score"])
                return {"intent": Intent.POLICY_QUESTION.value, "metrics": metrics}

        logger.info("[INTENT] Classified '%s...' -> %s", user_input[:40], intent.value)
        return {"intent": intent.value, "metrics": metrics}


def out_of_scope_placeholder(state: PolicyDeskState) -> dict:
    """Safe refusal and conversational capability steering fallback."""
    with trace_node("out_of_scope_placeholder", state) as metrics:
        if state.get("injection_detected"):
            return {
                "route": "out_of_scope",
                "answer": PROMPT_INJECTION_REFUSAL,
                "metrics": metrics,
            }

        user_input = (state.get("user_input") or "").lower()
        context_clarification = ""
        if any(w in user_input for w in ("pay", "fee", "cost", "charge")):
            context_clarification = (
                "Regarding payments or fees: claiming unclaimed deposits and activating dormant accounts "
                "is free under bank policy, while standard checking accounts require a $1,500 daily minimum balance "
                "to waive the $12 monthly fee ($5/month inactivity fee after 12 months).\n\n"
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
            "metrics": metrics,
        }
