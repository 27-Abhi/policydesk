import logging
import re
from typing import Optional
from collections import Counter

from app.graph.state import PolicyDeskState
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


def retrieve_parent_chunks(state: PolicyDeskState) -> dict:
    query = state.get("user_input", "")
    store = get_active_store()
    matched = store.retrieve(query, top_k=3)
    docs = [m["text"] for m in matched]

    logger.info("[RAG:RETRIEVE] Query: %r -> Found %d parent chunk(s)", query, len(matched))
    for idx, m in enumerate(matched, 1):
        clean_preview = m["text"][:75].replace("\n", " ")
        logger.info("  Chunk %d [%s] (Score: %.4f): %s...", idx, m["parent_id"], m["score"], clean_preview)

    return {"retrieved_docs": docs}


def grade_documents(state: PolicyDeskState) -> dict:
    query = state.get("user_input", "")
    docs = state.get("retrieved_docs") or []
    if not query or not docs:
        logger.warning("[RAG:GRADE] Empty query or retrieved docs. Failing relevance check.")
        return {"relevance_score": 0.0, "is_relevant": False}

    query_vec = Counter(_tokenize(query))
    best_score = 0.0

    for doc in docs:
        doc_vec = Counter(_tokenize(doc))
        score = _cosine_similarity(dict(query_vec), dict(doc_vec))
        if score > best_score:
            best_score = score

    is_relevant = best_score >= 0.15
    logger.info("[RAG:GRADE] Best score: %.4f (Threshold: 0.15, Relevant: %s)", best_score, is_relevant)
    return {
        "relevance_score": round(best_score, 4),
        "is_relevant": is_relevant,
    }


def rerank_documents(state: PolicyDeskState) -> dict:
    query = state.get("user_input", "")
    docs = state.get("retrieved_docs") or []
    if not docs:
        logger.info("[RAG:RERANK] No documents to rerank.")
        return {"reranked_docs": []}

    query_vec = Counter(_tokenize(query))
    scored = []
    for doc in docs:
        doc_vec = Counter(_tokenize(doc))
        score = _cosine_similarity(dict(query_vec), dict(doc_vec))
        scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    logger.info("[RAG:RERANK] Reranked %d chunk(s). Top score: %.4f", len(scored), scored[0][0])
    return {"reranked_docs": [doc for _, doc in scored]}


def generate_answer(state: PolicyDeskState) -> dict:
    is_relevant = state.get("is_relevant", False)
    reranked = state.get("reranked_docs") or []

    if is_relevant and reranked:
        top_context = reranked[0]
        answer = f"According to bank policy:\n\n{top_context}"
    else:
        answer = "No relevant policy documents found matching your inquiry."

    logger.info("[RAG:GENERATE] Answer synthesized (Relevant: %s, Length: %d chars)", is_relevant, len(answer))
    return {
        "answer": answer,
        "route": "policy_question",
    }

