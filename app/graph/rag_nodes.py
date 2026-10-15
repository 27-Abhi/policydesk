import logging
import math
import re
from typing import Optional
from collections import Counter

from app.graph.state import PolicyDeskState
from app.llm.client import get_llm_client
from app.prompts import POLICY_FALLBACK_ANSWER
from app.rag.store import ParentChildVectorStore, get_default_vector_store, _tokenize, _cosine_similarity

logger = logging.getLogger("policydesk.rag")
_DEFAULT_STORE: Optional[ParentChildVectorStore] = None


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
    query = state.get("user_input", "")
    messages = state.get("messages") or []

    search_query = query
    lower_query = query.lower().strip()
    follow_up_triggers = ("and ", "what if ", "what about ", "why ", "if i ", "what happens if", "so what", "how about ", "do i have to")
    is_follow_up = any(lower_query.startswith(p) for p in follow_up_triggers) or (len(lower_query.split()) < 6 and any(w in lower_query for w in ("pay", "cost", "fine", "penalty", "fee", "close", "claim", "wait")))

    if is_follow_up and messages:
        prior_topic = _extract_prior_topic(messages)
        if prior_topic and not any(w in lower_query for w in prior_topic.split()):
            search_query = f"{prior_topic} {query}"
            logger.info("[RAG:RETRIEVE] Contextualized elliptical query: %r -> %r", query, search_query)

    store = get_active_store()
    matched = store.retrieve(search_query, top_k=3, expand_window=1)
    docs = [m["text"] for m in matched]

    logger.info("[RAG:RETRIEVE] Query: %r -> Found %d parent chunk(s)", search_query, len(matched))
    for idx, m in enumerate(matched, 1):
        clean_preview = m["text"][:75].replace("\n", " ")
        logger.info("  Chunk %d [%s] (Score: %.4f): %s...", idx, m["parent_id"], m["score"], clean_preview)

    return {"retrieved_docs": docs, "contextual_query": search_query}


def _score_docs(query: str, docs: list[str]) -> list[tuple[float, str]]:
    if not query or not docs:
        return []
    store = get_active_store()
    q_tokens = _tokenize(query)
    if store:
        q_tokens = store.resolve_tokens(q_tokens)
    q_counts = Counter(q_tokens)
    scored = []
    for d in docs:
        d_tokens = _tokenize(d)
        d_counts = Counter(d_tokens)
        common = set(q_counts.keys()) & set(d_counts.keys())
        if not common:
            scored.append((0.0, d))
            continue
        dot = sum(q_counts[w] * d_counts[w] * (store.get_idf(w) ** 2) for w in common)
        norm1 = math.sqrt(sum((q_counts[w] * store.get_idf(w)) ** 2 for w in q_counts))
        norm2 = math.sqrt(sum((d_counts[w] * store.get_idf(w)) ** 2 for w in d_counts))
        if norm1 == 0.0 or norm2 == 0.0:
            scored.append((0.0, d))
            continue
        cosine = dot / (norm1 * norm2)
        coverage = len(common) / len(q_counts)
        score = cosine * (0.5 + 0.5 * coverage)
        scored.append((score, d))
    return sorted(scored, key=lambda x: x[0], reverse=True)


def grade_documents(state: PolicyDeskState) -> dict:
    query = state.get("contextual_query") or state.get("user_input", "")
    docs = state.get("retrieved_docs") or []
    scored = _score_docs(query, docs)
    best_score = scored[0][0] if scored else 0.0
    is_relevant = best_score >= 0.15
    logger.info("[RAG:GRADE] Best score: %.4f (Relevant: %s)", best_score, is_relevant)
    return {
        "relevance_score": round(best_score, 4),
        "is_relevant": is_relevant,
    }


def rerank_documents(state: PolicyDeskState) -> dict:
    query = state.get("contextual_query") or state.get("user_input", "")
    docs = state.get("retrieved_docs") or []
    scored = _score_docs(query, docs)
    logger.info("[RAG:RERANK] Reranked %d chunk(s)", len(scored))
    return {"reranked_docs": [doc for _, doc in scored]}


def generate_answer(state: PolicyDeskState) -> dict:
    is_relevant = state.get("is_relevant", False)
    reranked = state.get("reranked_docs") or []
    query = state.get("user_input", "")

    if is_relevant and reranked:
        combined_context = "\n\n---\n\n".join(reranked[:3])
        client = get_llm_client()
        answer = client.generate(query=query, context=combined_context)
    else:
        answer = POLICY_FALLBACK_ANSWER

    logger.info("[RAG:GENERATE] Answer synthesized (Relevant: %s, Length: %d chars)", is_relevant, len(answer))
    return {
        "answer": answer,
        "route": "policy_question",
    }

