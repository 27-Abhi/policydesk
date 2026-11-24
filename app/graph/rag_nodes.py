"""Agentic Corrective RAG (CRAG) pipeline with query rewriting, reranking, citations, and grounding verification."""

import logging
import os
import re
from typing import Any, Optional

from app.graph.state import PolicyDeskState
from app.llm.client import DeterministicMockClient, get_llm_client
from app.observability import trace_node
from app.prompts import GROUNDING_CHECK_PROMPT, POLICY_FALLBACK_ANSWER, QUERY_REWRITE_PROMPT
from app.rag.reranker import rerank_passages, score_parent_passage
from app.rag.store import ParentChildVectorStore, _tokenize, get_default_vector_store

logger = logging.getLogger("policydesk.rag")

MAX_RETRIEVAL_ATTEMPTS = 2
MAX_GENERATION_ATTEMPTS = 2
RELEVANCE_THRESHOLD = 0.15

_DEFAULT_STORE: Optional[ParentChildVectorStore] = None

_REWRITE_EXPANSIONS: dict[str, str] = {
    "online": "digital account opening digitally",
    "fd": "term deposit fixed deposit",
    "early": "premature withdrawal before maturity",
    "before maturity": "premature withdrawal",
    "turn 18": "minor attaining majority conversion",
    "18": "majority age minor",
    "person": "one individual nominee",
    "dormant": "inoperative unclaimed deposit DEAF",
    "no activity": "inoperative unclaimed 10 years DEAF",
    "avoid tds": "Form 15G 15H exemption threshold TDS",
    "dead": "deceased depositor settlement legal heir",
    "missing": "presumed dead missing person",
}


def get_active_store() -> ParentChildVectorStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = get_default_vector_store()
    return _DEFAULT_STORE


def set_active_store(store: Optional[ParentChildVectorStore]) -> None:
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


def _extract_prior_topic(messages: list[dict[str, str]]) -> str:
    """Extract key policy domain nouns from previous user turns in conversation."""
    if not messages:
        return ""
    user_turns = [m["content"] for m in messages if m.get("role") == "user"]
    if len(user_turns) < 2:
        return ""
    prev_user_turn = user_turns[-2]
    tokens = _tokenize(prev_user_turn)
    topic_words = [w for w in tokens if w not in ("what", "happen", "tell", "explain", "know", "question", "pleas", "bank")]
    return " ".join(topic_words)


def retrieve_parent_chunks(state: PolicyDeskState) -> dict:
    """Retrieve candidate parent policy chunks and citation metadata."""
    with trace_node("retrieve_parent_chunks", state) as metrics:
        attempt = int(state.get("retrieval_attempts") or 0) + 1
        base_query = state.get("user_input", "")
        rewritten = state.get("rewritten_query")

        if attempt > 1 and rewritten:
            search_query = rewritten
        else:
            messages = state.get("messages") or state.get("conversation_history") or []
            search_query = base_query
            lower_query = base_query.lower().strip()
            follow_up_triggers = (
                "and ", "what if ", "what about ", "why ", "if i ",
                "what happens if", "so what", "how about ", "do i have to",
            )
            is_follow_up = any(lower_query.startswith(p) for p in follow_up_triggers) or (
                len(lower_query.split()) < 6 and any(w in lower_query for w in ("pay", "cost", "fine", "penalty", "fee", "close", "claim", "wait"))
            )
            if is_follow_up and messages:
                prior_topic = _extract_prior_topic(messages)
                if prior_topic and not any(w in lower_query for w in prior_topic.split()):
                    search_query = f"{prior_topic} {base_query}"
                    logger.info("[RAG:RETRIEVE] Contextualized elliptical query: %r -> %r", base_query, search_query)

        store = get_active_store()
        matched = store.retrieve(search_query, top_k=5, expand_window=1)
        docs = [m["text"] for m in matched]
        citations = [
            {
                "parent_id": m.get("parent_id"),
                "doc_id": m.get("doc_id"),
                "title": m.get("title"),
                "section": m.get("section", "General Policy"),
                "page": m.get("page"),
                "citation": m.get("citation"),
                "score": m.get("score", 0.0),
                "bm25_score": m.get("bm25_score", 0.0),
                "dense_score": m.get("dense_score", 0.0),
            }
            for m in matched
        ]

        metrics["retrieval_attempts"] = attempt
        logger.info("[RAG:RETRIEVE] Attempt %d Query: %r -> Found %d chunk(s)", attempt, search_query, len(matched))
        for idx, m in enumerate(matched, 1):
            clean_preview = m["text"][:75].replace("\n", " ")
            logger.info("  Chunk %d [%s] (Score: %.4f): %s...", idx, m["parent_id"], m["score"], clean_preview)

        return {
            "retrieved_docs": docs,
            "citations": citations,
            "contextual_query": search_query,
            "retrieval_attempts": attempt,
            "metrics": metrics,
        }


def grade_documents(state: PolicyDeskState) -> dict:
    """Grade retrieved passages against query intent and threshold."""
    with trace_node("grade_documents", state) as metrics:
        query = state.get("contextual_query") or state.get("user_input", "")
        docs = state.get("retrieved_docs") or []
        citations = state.get("citations") or []
        store = get_active_store()

        if citations and len(citations) == len(docs):
            _, _, best_rerank = rerank_passages(query, docs, citations=citations, store=store)
            best_retrieval = max((float(c.get("score", 0.0)) for c in citations), default=0.0)
            best_score = max(best_rerank, best_retrieval)
        else:
            scores = [score_parent_passage(query, d, store=store) for d in docs]
            best_score = max(scores, default=0.0)

        is_relevant = best_score >= RELEVANCE_THRESHOLD
        logger.info("[RAG:GRADE] Best score: %.4f (Relevant: %s)", best_score, is_relevant)
        return {
            "relevance_score": round(best_score, 4),
            "is_relevant": is_relevant,
            "metrics": metrics,
        }


def rewrite_query(state: PolicyDeskState) -> dict:
    """Rewrite a poor-scoring policy query into formal banking terms for bounded CRAG retry."""
    with trace_node("rewrite_query", state) as metrics:
        orig = state.get("contextual_query") or state.get("user_input", "")
        orig_lower = orig.lower()

        extra_terms: list[str] = []
        for trigger, expansion in _REWRITE_EXPANSIONS.items():
            if trigger in orig_lower:
                extra_terms.append(expansion)

        client = get_llm_client()
        rewritten = ""
        if not isinstance(client, DeterministicMockClient):
            parsed = client.complete_json(QUERY_REWRITE_PROMPT, orig, max_tokens=90)
            if parsed and isinstance(parsed.get("rewritten_query"), str):
                rewritten = parsed["rewritten_query"].strip()

        if not rewritten:
            rewritten = f"{orig} {' '.join(extra_terms)}".strip() if extra_terms else f"bank deposit policy rules {orig}".strip()

        logger.info("[RAG:REWRITE] %r -> %r", orig, rewritten)
        return {
            "rewritten_query": rewritten,
            "metrics": metrics,
        }


def rerank_documents(state: PolicyDeskState) -> dict:
    """Stage-2 semantic and cross-feature reranking of retrieved parent passages."""
    with trace_node("rerank_documents", state) as metrics:
        query = state.get("contextual_query") or state.get("user_input", "")
        docs = state.get("retrieved_docs") or []
        citations = state.get("citations") or []
        store = get_active_store()

        reranked_docs, reranked_cits, _ = rerank_passages(
            query=query,
            docs=docs,
            citations=citations,
            store=store,
        )
        top_docs = reranked_docs[:3]
        top_cits = reranked_cits[:3]
        logger.info("[RAG:RERANK] Reranked %d chunk(s), kept top %d", len(docs), len(top_docs))
        return {
            "reranked_docs": top_docs,
            "citations": top_cits,
            "metrics": metrics,
        }


def _format_context_with_citations(reranked: list[str], citations: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for idx, doc_text in enumerate(reranked[:3]):
        cit = citations[idx] if idx < len(citations) else {}
        label = cit.get("citation") or cit.get("title") or ""
        section = cit.get("section") or ""
        if label:
            tag = f"[Source: {label}" + (f" | {section}" if section and section != "General Policy" else "") + "]"
            parts.append(f"{tag}\n{doc_text}")
        else:
            parts.append(doc_text)
    return "\n\n---\n\n".join(parts)


def generate_answer(state: PolicyDeskState) -> dict:
    """Synthesize a concise, cited answer from reranked policy evidence."""
    with trace_node("generate_answer", state) as metrics:
        attempt = int(state.get("generation_attempts") or 0) + 1
        is_relevant = state.get("is_relevant", False)
        reranked = state.get("reranked_docs") or []
        citations = state.get("citations") or []
        query = state.get("user_input", "")

        if is_relevant and reranked:
            combined_context = _format_context_with_citations(reranked, citations)
            if state.get("defer_generation"):
                metrics["generation_attempts"] = attempt
                return {
                    "answer": combined_context,
                    "route": "policy_question",
                    "generation_attempts": attempt,
                    "metrics": metrics,
                }
            if attempt > 1 and state.get("grounding_reason"):
                query = f"{query} (Be strictly faithful to context figures and cite [Source: ...]; avoid unsupported claims.)"
            client = get_llm_client()
            answer = client.generate(query=query, context=combined_context)
            metrics["usage"] = dict(client.last_usage)
        else:
            answer = POLICY_FALLBACK_ANSWER

        metrics["generation_attempts"] = attempt
        logger.info("[RAG:GENERATE] Attempt %d (Relevant: %s, Length: %d chars)", attempt, is_relevant, len(answer))
        return {
            "answer": answer,
            "route": "policy_question",
            "generation_attempts": attempt,
            "metrics": metrics,
        }


def _verify_numeric_grounding(answer: str, evidence: str) -> tuple[bool, str]:
    """Ensure dollar figures, percentages, and key numbers in the answer appear in retrieved evidence."""
    # Ignore citation references like [Source: ... (comprehensive_deposit_policy_p12, p.4)]
    ans_clean = re.sub(r"\[Source:[^\]]+\]", "", answer)
    ev_clean = re.sub(r"\[Source:[^\]]+\]", "", evidence).lower()

    # Check currency amounts and percentages strictly
    critical_nums = re.findall(r"(?:\$|₹|rs\.?\s*)[0-9,]+(?:\.[0-9]+)?|\b\d+(?:\.\d+)?%", ans_clean, re.IGNORECASE)
    for num in critical_nums:
        digits = re.sub(r"[^0-9.]", "", num)
        if digits and digits not in ev_clean.replace(",", ""):
            return False, f"Numeric claim {num!r} not found in retrieved policy evidence."

    return True, "All claims grounded in retrieved evidence."


def verify_grounding(state: PolicyDeskState) -> dict:
    """Post-generation faithfulness verification against reranked policy clauses."""
    with trace_node("verify_grounding", state) as metrics:
        answer = state.get("answer") or ""
        reranked = state.get("reranked_docs") or []
        is_relevant = bool(state.get("is_relevant", False))

        if not is_relevant or not reranked or answer == POLICY_FALLBACK_ANSWER:
            return {
                "grounded": False,
                "grounding_reason": "No relevant evidence available.",
                "metrics": metrics,
            }

        if state.get("defer_generation"):
            return {
                "grounded": True,
                "grounding_reason": "Deferred for live token streaming.",
                "metrics": metrics,
            }

        evidence = "\n\n".join(reranked[:3])
        num_ok, num_reason = _verify_numeric_grounding(answer, evidence)
        if not num_ok:
            logger.warning("[RAG:VERIFY] Grounding failed: %s", num_reason)
            return {
                "grounded": False,
                "grounding_reason": num_reason,
                "metrics": metrics,
            }

        if os.getenv("ENABLE_LLM_RERANK", "false").strip().lower() in ("1", "true", "yes"):
            client = get_llm_client()
            if not isinstance(client, DeterministicMockClient):
                parsed = client.complete_json(
                    GROUNDING_CHECK_PROMPT,
                    f"Context:\n{evidence}\n\nDraft Answer:\n{answer}",
                    max_tokens=120,
                )
                if parsed and parsed.get("grounded") is False:
                    reason = str(parsed.get("reason") or "LLM grounding verifier flagged unsupported claim.")
                    logger.warning("[RAG:VERIFY:LLM] Grounding rejected: %s", reason)
                    return {
                        "grounded": False,
                        "grounding_reason": reason,
                        "metrics": metrics,
                    }

        logger.info("[RAG:VERIFY] Answer passed grounding check.")
        return {
            "grounded": True,
            "grounding_reason": num_reason,
            "metrics": metrics,
        }


def abstain_policy_answer(state: PolicyDeskState) -> dict:
    """Fail-closed fallback when CRAG retries or grounding verification cannot support an answer."""
    with trace_node("abstain_policy_answer", state) as metrics:
        logger.info("[RAG:ABSTAIN] Falling back to safe policy abstention.")
        return {
            "answer": POLICY_FALLBACK_ANSWER,
            "route": "policy_question",
            "grounded": False,
            "metrics": metrics,
        }
