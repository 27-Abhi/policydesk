"""Stage-2 multi-signal passage reranker and optional LLM cross-verification."""

import logging
import os
import re
from typing import Any, Optional

from app.rag.embeddings import cosine_similarity_dense
from app.rag.store import ParentChildVectorStore, _extract_bigrams, _has_non_deposit_product_mismatch, _tokenize

logger = logging.getLogger("policydesk.rag.reranker")


def score_parent_passage(
    query: str,
    passage: str,
    store: Optional[ParentChildVectorStore] = None,
    retrieval_score: Optional[float] = None,
    section: str = "",
) -> float:
    """Score a parent passage using dense alignment, IDF coverage, bigram matching, and domain fit."""
    if not query or not passage:
        return 0.0

    q_tokens = _tokenize(query)
    if store is not None:
        q_tokens = store.resolve_tokens(q_tokens)
    if not q_tokens:
        return 0.0

    p_tokens = _tokenize(passage)
    if not p_tokens:
        return 0.0

    q_set = set(q_tokens)
    p_set = set(p_tokens)
    common = q_set & p_set

    # Distinctive IDF coverage across the query tokens
    total_idf = 0.0
    matched_idf = 0.0
    for tok in q_set:
        idf = store.get_idf(tok) if store is not None else 1.5
        total_idf += idf
        if tok in p_set:
            matched_idf += idf
    lexical_coverage = (matched_idf / total_idf) if total_idf > 0.0 else 0.0

    # Bigram exactness bonus
    q_bigrams = set(_extract_bigrams(q_tokens))
    p_bigrams = set(_extract_bigrams(p_tokens))
    bigram_ratio = (len(q_bigrams & p_bigrams) / len(q_bigrams)) if q_bigrams else 0.0

    # Section title alignment bonus
    section_bonus = 0.0
    if section:
        s_set = set(_tokenize(section))
        if s_set & q_set:
            section_bonus = 0.08 * (len(s_set & q_set) / max(1, len(q_set)))

    # Dense semantic similarity over full passage
    dense_sim = 0.0
    if store is not None:
        q_vec = store.embedder.embed(query)
        p_vec = store.embedder.embed(f"{section} {passage[:700]}")
        dense_sim = cosine_similarity_dense(q_vec, p_vec)

    base = 0.48 * lexical_coverage + 0.22 * dense_sim + 0.18 * bigram_ratio + section_bonus
    if retrieval_score is not None:
        base = 0.55 * base + 0.45 * retrieval_score

    is_oos = store.is_out_of_domain_query(query) if store is not None else _has_non_deposit_product_mismatch(query.lower())
    if is_oos:
        base *= 0.40

    if not common and dense_sim < 0.35:
        base *= 0.5

    return round(min(1.0, max(0.0, base)), 4)


def rerank_passages(
    query: str,
    docs: list[str],
    citations: Optional[list[dict[str, Any]]] = None,
    store: Optional[ParentChildVectorStore] = None,
) -> tuple[list[str], list[dict[str, Any]], float]:
    """Rerank retrieved parent passages and preserve aligned citation metadata."""
    if not query or not docs:
        return [], [], 0.0

    cit_list = citations if citations and len(citations) == len(docs) else [{} for _ in docs]
    scored_rows: list[tuple[float, str, dict[str, Any]]] = []

    for doc_text, cit in zip(docs, cit_list):
        r_score = cit.get("score") if isinstance(cit, dict) else None
        section = cit.get("section", "") if isinstance(cit, dict) else ""
        score = score_parent_passage(
            query=query,
            passage=doc_text,
            store=store,
            retrieval_score=r_score,
            section=section,
        )
        scored_rows.append((score, doc_text, cit))

    scored_rows.sort(key=lambda item: item[0], reverse=True)
    best_score = scored_rows[0][0] if scored_rows else 0.0

    # Optional LLM listwise verification when explicitly enabled
    if os.getenv("ENABLE_LLM_RERANK", "false").strip().lower() in ("1", "true", "yes") and len(scored_rows) > 1:
        scored_rows = _optional_llm_rerank(query, scored_rows)

    reranked_docs = [row[1] for row in scored_rows]
    reranked_cits = [dict(row[2], rerank_score=row[0]) for row in scored_rows]
    return reranked_docs, reranked_cits, best_score


def _optional_llm_rerank(
    query: str,
    scored_rows: list[tuple[float, str, dict[str, Any]]],
) -> list[tuple[float, str, dict[str, Any]]]:
    try:
        from app.llm.client import DeterministicMockClient, get_llm_client

        client = get_llm_client()
        if isinstance(client, DeterministicMockClient):
            return scored_rows

        snippets = "\n".join(
            f"[{idx}] {re.sub(r'\\s+', ' ', row[1])[:260]}"
            for idx, row in enumerate(scored_rows[:5])
        )
        prompt = (
            f"Query: {query}\n\n"
            f"Passages:\n{snippets}\n\n"
            "Return JSON with the passage indices sorted best-first: {\"order\": [0, 1, ...]}"
        )
        parsed = client.complete_json(
            system_prompt="You rank policy passages by relevance to the user query. Return valid JSON only.",
            user_prompt=prompt,
        )
        if parsed and isinstance(parsed.get("order"), list):
            ordered: list[tuple[float, str, dict[str, Any]]] = []
            seen: set[int] = set()
            for idx in parsed["order"]:
                if isinstance(idx, int) and 0 <= idx < len(scored_rows) and idx not in seen:
                    ordered.append(scored_rows[idx])
                    seen.add(idx)
            for idx, row in enumerate(scored_rows):
                if idx not in seen:
                    ordered.append(row)
            return ordered
    except Exception as exc:
        logger.debug("[RAG:RERANK:LLM] Skipped optional LLM rerank: %s", exc)
    return scored_rows
