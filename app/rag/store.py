"""Hybrid parent-child vector store combining Okapi BM25, dense semantic vectors, and RRF fusion."""

import difflib
import math
import os
import re
from collections import Counter
from typing import Any, Optional

from app.rag.embeddings import (
    BaseEmbeddingProvider,
    SqliteVecIndex,
    cosine_similarity_dense,
    get_embedding_provider,
)

STOPWORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "ain", "all", "am", "an", "and",
    "any", "are", "aren", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "couldn", "d", "did", "didn",
    "do", "does", "doesn", "doing", "don", "down", "during", "each", "few", "for",
    "from", "further", "had", "hadn", "has", "hasn", "have", "haven", "having", "he",
    "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in",
    "into", "is", "isn", "it", "its", "itself", "just", "ll", "m", "ma", "me", "mightn",
    "more", "most", "mustn", "my", "myself", "needn", "no", "nor", "not", "now", "o",
    "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out",
    "over", "own", "re", "s", "same", "shan", "she", "should", "shouldn", "so", "some",
    "such", "t", "than", "that", "the", "their", "theirs", "them", "themselves", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "ve", "very", "was", "wasn", "we", "were", "weren", "what", "when", "where",
    "which", "while", "who", "whom", "why", "will", "with", "won", "wouldn", "y", "you",
    "your", "yours", "yourself", "yourselves",
})

# Query-side domain expansions mapping colloquial user terms to formal policy terminology
_SYNONYM_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "online": ("digital", "digitally"),
    "fd": ("term", "deposit"),
    "fds": ("term", "deposit"),
    "person": ("individual",),
    "people": ("individual",),
    "18": ("majority", "major"),
    "turn": ("attaining", "majority"),
    "early": ("premature",),
    "break": ("premature", "withdrawal"),
    "dormant": ("inoperative",),
    "inoperative": ("dormant",),
    "activity": ("inoperative", "unclaimed", "deaf"),
    "transaction": ("operation", "activity"),
    "die": ("deceased", "death"),
    "dies": ("deceased", "death"),
    "dead": ("deceased", "death"),
    "avoid": ("exemption", "form", "15g"),
    "split": ("bifurcate", "multiple"),
}

# Explicit non-deposit financial product markers that should fail relevance gating
_NON_DEPOSIT_PRODUCT_PHRASES: tuple[str, ...] = (
    "home loan",
    "car loan",
    "personal loan",
    "education loan",
    "income tax return",
    "file income tax",
    "life insurance",
    "health insurance",
    "demat account",
    "stock trading",
    "stock market",
    "repo rate",
    "credit card",
    "cashback reward",
    "cryptocurrency",
    "passport",
    "chocolate cake",
    "best mutual funds",
)

_SECTION_RE = re.compile(
    r"(?:^|\n)\s*((?:\d{1,2}\.\s+[A-Z][A-Z0-9\s,&/\-()]{3,80})|(?:\([ivx]+\)\s+[A-Z][A-Za-z0-9\s,&/\-()]{3,80}))"
)


def _stem(word: str) -> str:
    for sfx, cut, min_len in (("sses", 2, 4), ("ies", 3, 3), ("es", 2, 3), ("ing", 3, 4), ("ed", 2, 3)):
        if word.endswith(sfx) and len(word) > min_len:
            return (word[:-cut] + "y") if sfx == "ies" else word[:-cut]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 2:
        return word[:-1]
    if word.endswith("e") and not word.endswith("ee") and len(word) > 3:
        return word[:-1]
    return word


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"\b[a-zA-Z0-9_]+\b", text.lower())
    return [_stem(w) for w in words if w not in STOPWORDS and len(w) > 1]


def _extract_bigrams(tokens: list[str]) -> list[str]:
    if len(tokens) < 2:
        return []
    return [f"{tokens[i]}_{tokens[i + 1]}" for i in range(len(tokens) - 1)]


def _cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    if not vec1 or not vec2:
        return 0.0
    common = set(vec1.keys()) & set(vec2.keys())
    if not common:
        return 0.0
    dot_product = sum(vec1[w] * vec2[w] for w in common)
    norm1 = math.sqrt(sum(v * v for v in vec1.values()))
    norm2 = math.sqrt(sum(v * v for v in vec2.values()))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot_product / (norm1 * norm2)


def _has_non_deposit_product_mismatch(
    query_lower: str,
    df: Optional[Counter] = None,
    bigram_df: Optional[Counter] = None,
) -> bool:
    for phrase in _NON_DEPOSIT_PRODUCT_PHRASES:
        if phrase not in query_lower:
            continue
        tokens = _tokenize(phrase)
        if not tokens:
            return True
        if len(tokens) == 1:
            if df is not None and tokens[0] in df:
                continue
            return True
        bigrams = _extract_bigrams(tokens)
        if bigram_df is not None and bigrams and all(bg in bigram_df for bg in bigrams):
            continue
        return True
    return False


class ParentChildVectorStore:
    """Hybrid parent-child store using Okapi BM25, dense semantic embeddings, sqlite-vec, and RRF."""

    def __init__(
        self,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        vec_db_path: str = ":memory:",
    ) -> None:
        self.parents: dict[str, dict[str, Any]] = {}
        self.children: list[dict[str, Any]] = []
        self.df: Counter = Counter()
        self.bigram_df: Counter = Counter()
        self.doc_keywords: dict[str, set[str]] = {}
        self._total_child_tokens: int = 0
        self.embedder: BaseEmbeddingProvider = embedding_provider or get_embedding_provider()
        self.vec_index = SqliteVecIndex(db_path=vec_db_path, dim=self.embedder.dim)

    @property
    def avg_child_len(self) -> float:
        if not self.children:
            return 1.0
        return max(1.0, self._total_child_tokens / len(self.children))

    def get_corpus_keywords(self) -> set[str]:
        corpus_kw: set[str] = set()
        for kws in self.doc_keywords.values():
            corpus_kw.update(kws)
        return corpus_kw

    def get_idf(self, word: str) -> float:
        total = len(self.children)
        if total == 0:
            return 1.0
        n = self.df.get(word, 0)
        return math.log((total - n + 0.5) / (n + 0.5) + 1.0)

    def _split_into_chunks(self, text: str, chunk_size: int = 500) -> list[str]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks = []
        for p in paragraphs:
            if len(p) <= chunk_size:
                chunks.append(p)
            else:
                chunks.extend(self._split_sentences(p, child_size=chunk_size))
        return chunks if chunks else [text.strip()]

    def _split_sentences(self, text: str, child_size: int = 150) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            return [text.strip()]
        chunks = []
        current = ""
        for s in sentences:
            if not current:
                current = s
            elif len(current) + len(s) + 1 <= child_size:
                current += " " + s
            else:
                chunks.append(current)
                current = s
        if current:
            chunks.append(current)
        return chunks

    def remove_document(self, doc_id: str) -> None:
        pids_to_remove = [pid for pid, p in self.parents.items() if p["doc_id"] == doc_id]
        for pid in pids_to_remove:
            del self.parents[pid]
        new_children = []
        for child in self.children:
            if child["parent_id"].startswith(f"{doc_id}_p"):
                self.df.subtract(child["vector"].keys())
                self.bigram_df.subtract(child.get("bigrams", {}).keys())
                self._total_child_tokens = max(0, self._total_child_tokens - child.get("length", 0))
            else:
                new_children.append(child)
        self.children = new_children
        self.df += Counter()
        self.bigram_df += Counter()
        self.doc_keywords.pop(doc_id, None)
        self.vec_index.delete_doc(doc_id)

    @staticmethod
    def _resolve_page_number(chunk_text: str, page_spans: Optional[list[tuple[int, str]]]) -> Optional[int]:
        if not page_spans:
            return None
        snippet = chunk_text[:60].strip()
        if not snippet:
            return page_spans[0][0]
        for p_num, p_text in page_spans:
            if snippet in p_text:
                return p_num
        # Fallback to largest token overlap across pages
        words = set(re.findall(r"\b[a-zA-Z0-9]{4,}\b", chunk_text.lower()))
        if not words:
            return None
        best_page = page_spans[0][0]
        best_hits = -1
        for p_num, p_text in page_spans:
            p_low = p_text.lower()
            hits = sum(1 for w in words if w in p_low)
            if hits > best_hits:
                best_hits = hits
                best_page = p_num
        return best_page

    def add_document(
        self,
        doc_id: str,
        title: str,
        text: str,
        page_spans: Optional[list[tuple[int, str]]] = None,
    ) -> tuple[int, int]:
        self.remove_document(doc_id)

        kw_set: set[str] = set()
        for w in re.findall(r"\b[a-zA-Z0-9_]{3,}\b", title.lower()):
            if w not in STOPWORDS:
                kw_set.add(w)
                kw_set.add(_stem(w))

        words = [w for w in re.findall(r"\b[a-zA-Z0-9_]{4,}\b", text.lower()) if w not in STOPWORDS]
        for w, cnt in Counter(words).items():
            if cnt >= 2:
                kw_set.add(w)
                kw_set.add(_stem(w))
        self.doc_keywords[doc_id] = kw_set

        parent_texts = self._split_into_chunks(text, chunk_size=500)
        num_parents = 0
        num_children = 0
        current_section = "General Policy"

        child_batch_records: list[tuple[str, str]] = []
        child_objs: list[dict[str, Any]] = []

        for p_idx, p_text in enumerate(parent_texts):
            sec_match = _SECTION_RE.search(p_text)
            if sec_match:
                current_section = re.sub(r"\s+", " ", sec_match.group(1)).strip()[:90]

            page_num = self._resolve_page_number(p_text, page_spans)
            parent_id = f"{doc_id}_p{p_idx}"
            citation = f"{title} ({parent_id}" + (f", p.{page_num}" if page_num else "") + ")"

            self.parents[parent_id] = {
                "parent_id": parent_id,
                "doc_id": doc_id,
                "seq_idx": p_idx,
                "title": title,
                "section": current_section,
                "page": page_num,
                "citation": citation,
                "text": p_text,
            }
            num_parents += 1

            child_texts = self._split_sentences(p_text, child_size=160)
            for c_idx, c_text in enumerate(child_texts):
                tokens = _tokenize(c_text)
                tf = dict(Counter(tokens))
                bigrams = dict(Counter(_extract_bigrams(tokens)))
                child_id = f"{parent_id}_c{c_idx}"

                child_record = {
                    "child_id": child_id,
                    "parent_id": parent_id,
                    "doc_id": doc_id,
                    "title": title,
                    "section": current_section,
                    "text": c_text,
                    "vector": tf,
                    "bigrams": bigrams,
                    "length": len(tokens),
                }
                child_objs.append(child_record)
                child_batch_records.append((child_id, f"{current_section} {c_text}"))

                self.df.update(tf.keys())
                self.bigram_df.update(bigrams.keys())
                self._total_child_tokens += len(tokens)
                num_children += 1

        if child_batch_records:
            vectors = self.embedder.embed_batch([txt for _, txt in child_batch_records])
            for child_record, (cid, _), vec in zip(child_objs, child_batch_records, vectors):
                child_record["dense_vector"] = vec
                self.children.append(child_record)
                self.vec_index.upsert(cid, doc_id, vec)

        return num_parents, num_children

    def resolve_tokens(self, tokens: list[str]) -> list[str]:
        """Resolve minor spelling typos against indexed vocabulary using difflib."""
        resolved = []
        vocab = self.df.keys()
        for tok in tokens:
            if tok in self.df:
                resolved.append(tok)
            elif len(tok) >= 5:
                matches = difflib.get_close_matches(tok, vocab, n=1, cutoff=0.78)
                resolved.append(matches[0] if matches else tok)
            else:
                resolved.append(tok)
        return resolved

    def expand_query_tokens(self, raw_words: list[str], resolved_tokens: list[str]) -> list[str]:
        """Append controlled domain synonym stems while keeping original token list intact for coverage."""
        expanded = list(resolved_tokens)
        seen = set(resolved_tokens)
        for w in raw_words:
            for syn in _SYNONYM_EXPANSIONS.get(w, ()):
                syn_stem = _stem(syn)
                if syn_stem not in seen and syn_stem in self.df:
                    expanded.append(syn_stem)
                    seen.add(syn_stem)
        return expanded

    def score_child_bm25(
        self,
        query_tokens: list[str],
        expanded_tokens: list[str],
        query_bigrams: list[str],
        child: dict[str, Any],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> float:
        """Compute Okapi BM25 + bigram overlap + distinctive query coverage on a child chunk."""
        if not query_tokens:
            return 0.0

        c_counts = child["vector"]
        c_len = max(1, child.get("length", 1))
        avg_len = self.avg_child_len

        bm25 = 0.0
        for tok in set(expanded_tokens):
            tf = c_counts.get(tok, 0)
            if tf == 0:
                continue
            idf = self.get_idf(tok) ** 1.25
            weight = 1.0 if tok in query_tokens else 0.75
            denom = tf + k1 * (1.0 - b + b * (c_len / avg_len))
            bm25 += weight * idf * ((tf * (k1 + 1.0)) / denom)

        if bm25 <= 0.0:
            return 0.0

        # Bigram phrase bonus
        c_bigrams = child.get("bigrams", {})
        bigram_hits = sum(1 for bg in set(query_bigrams) if bg in c_bigrams)
        bigram_bonus = 1.0 + 0.18 * bigram_hits

        # Distinctive coverage over original (pre-expansion) query tokens
        total_q_idf = 0.0
        matched_q_idf = 0.0
        max_idf = math.log(len(self.children) + 1.5) + 0.5
        for tok in set(query_tokens):
            tok_idf = self.get_idf(tok) if tok in self.df else max_idf
            total_q_idf += tok_idf
            if tok in c_counts:
                matched_q_idf += tok_idf
            else:
                # Credit synonym expansion only if that synonym actually matched in this chunk
                for syn in _SYNONYM_EXPANSIONS.get(tok, ()):
                    if _stem(syn) in c_counts:
                        matched_q_idf += tok_idf * 0.85
                        break

        coverage = (matched_q_idf / total_q_idf) if total_q_idf > 0.0 else 0.0
        # Saturate BM25 cleanly into [0, 1] and combine with IDF-weighted query coverage
        bm25_norm = bm25 / (bm25 + 4.5)
        return bm25_norm * bigram_bonus * (0.35 + 0.65 * coverage)

    def score_child(self, query_tokens: list[str], child: dict[str, Any]) -> float:
        """Backward-compatible wrapper scoring a child chunk from tokenized query terms."""
        return self.score_child_bm25(
            query_tokens=query_tokens,
            expanded_tokens=query_tokens,
            query_bigrams=_extract_bigrams(query_tokens),
            child=child,
        )

    def is_out_of_domain_query(self, query: str) -> bool:
        """Check whether query targets an unsupported non-deposit product absent from indexed bigrams."""
        return _has_non_deposit_product_mismatch(query.lower(), df=self.df, bigram_df=self.bigram_df)

    def retrieve(self, query: str, top_k: int = 3, expand_window: int = 1) -> list[dict[str, Any]]:
        raw_words = re.findall(r"\b[a-zA-Z0-9_]+\b", query.lower())
        raw_filtered = [w for w in raw_words if w not in STOPWORDS and len(w) > 1]
        base_tokens = _tokenize(query)
        if not base_tokens:
            return []

        resolved_tokens = self.resolve_tokens(base_tokens)
        expanded_tokens = self.expand_query_tokens(raw_filtered, resolved_tokens)
        query_bigrams = _extract_bigrams(resolved_tokens)

        # 1. Sparse BM25 pass
        bm25_scores: dict[str, float] = {}
        for child in self.children:
            s = self.score_child_bm25(resolved_tokens, expanded_tokens, query_bigrams, child)
            if s > 0.0:
                bm25_scores[child["child_id"]] = s

        # 2. Dense semantic pass via sqlite-vec / embedding provider
        expanded_query_text = query + " " + " ".join(t for t in expanded_tokens if t not in resolved_tokens)
        q_vec = self.embedder.embed(expanded_query_text.strip())
        dense_scores = self.vec_index.search(q_vec)

        # 3. Reciprocal Rank Fusion (RRF) over top sparse + top dense candidates
        bm25_ranked = sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)
        dense_ranked = sorted(dense_scores.items(), key=lambda x: x[1], reverse=True)

        rrf_k = 60.0
        rrf_scores: dict[str, float] = {}
        for rank, (cid, _) in enumerate(bm25_ranked[:40], start=1):
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.2 / (rrf_k + rank))
        for rank, (cid, d_score) in enumerate(dense_ranked[:40], start=1):
            if d_score >= 0.25:
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (0.8 / (rrf_k + rank))

        if not rrf_scores and bm25_ranked:
            for rank, (cid, _) in enumerate(bm25_ranked[:20], start=1):
                rrf_scores[cid] = 1.0 / (rrf_k + rank)

        oos_penalty = 0.30 if self.is_out_of_domain_query(query) else 1.0

        child_map = {c["child_id"]: c for c in self.children}
        scored_children: list[tuple[float, float, dict[str, Any]]] = []
        for cid, rrf in rrf_scores.items():
            child = child_map.get(cid)
            if not child:
                continue
            b_score = bm25_scores.get(cid, 0.0)
            d_score = dense_scores.get(cid, 0.0)
            # Calibrated hybrid confidence score (used by 0.1500 relevance gate)
            hybrid_score = (0.72 * b_score + 0.28 * max(0.0, d_score - 0.15)) * oos_penalty
            # Scale slightly by RRF rank signal so multi-modal hits break ties cleanly
            rank_sort_key = rrf * 10.0 + hybrid_score
            scored_children.append((rank_sort_key, hybrid_score, child))

        scored_children.sort(key=lambda x: x[0], reverse=True)

        seen_parents: set[str] = set()
        seen_texts: set[str] = set()
        matched_parents: list[dict[str, Any]] = []

        for _, hybrid_score, child in scored_children:
            parent_id = child["parent_id"]
            if parent_id in seen_parents:
                continue

            parent_info = dict(self.parents[parent_id])
            norm_text = re.sub(r"\s+", " ", parent_info["text"]).strip().lower()
            if norm_text in seen_texts:
                continue

            seen_parents.add(parent_id)
            seen_texts.add(norm_text)

            cid = child["child_id"]
            parent_info["score"] = round(hybrid_score, 4)
            parent_info["bm25_score"] = round(bm25_scores.get(cid, 0.0), 4)
            parent_info["dense_score"] = round(dense_scores.get(cid, 0.0), 4)
            parent_info["rrf_score"] = round(rrf_scores.get(cid, 0.0), 6)

            if expand_window > 0:
                doc_id = parent_info.get("doc_id", "")
                seq_idx = parent_info.get("seq_idx", 0)
                expanded_texts = [parent_info["text"]]
                expanded_pids = [parent_id]

                for step in range(1, expand_window + 1):
                    next_pid = f"{doc_id}_p{seq_idx + step}"
                    if next_pid in self.parents:
                        next_chunk = self.parents[next_pid]
                        next_norm = re.sub(r"\s+", " ", next_chunk["text"]).strip().lower()
                        if next_norm not in seen_texts:
                            expanded_texts.append(next_chunk["text"])
                            expanded_pids.append(next_pid)
                            seen_parents.add(next_pid)
                            seen_texts.add(next_norm)

                parent_info["text"] = "\n\n".join(expanded_texts)
                parent_info["expanded_pids"] = expanded_pids

            matched_parents.append(parent_info)
            if len(matched_parents) >= top_k:
                break

        return matched_parents

    def list_documents(self) -> list[dict[str, Any]]:
        docs_map: dict[str, dict[str, Any]] = {}
        for p in self.parents.values():
            did = p["doc_id"]
            if did not in docs_map:
                docs_map[did] = {"doc_id": did, "title": p["title"], "parent_chunks": 0}
            docs_map[did]["parent_chunks"] += 1
        return list(docs_map.values())


DEFAULT_POLICIES = [
    {
        "doc_id": "wire_transfer",
        "title": "Wire Transfer Policy",
        "text": (
            "Wire transfer processing rules and fees for PolicyDesk Financial.\n\n"
            "Domestic wire transfers incur a $25 fee and are processed within 2 to 4 hours. "
            "International wire transfers incur a $45 fee and require up to 48 hours to settle.\n\n"
            "The daily cutoff time for submitting wire transfers is 4:00 PM EST. "
            "Wires submitted after 4:00 PM EST will be executed on the next business day. "
            "Standard accounts have a daily wire transfer limit of $50,000. "
            "Premium accounts have an expanded daily limit of $250,000."
        ),
    },
    {
        "doc_id": "account_maintenance",
        "title": "Account Balance and Maintenance Policy",
        "text": (
            "Checking and savings account balance requirements and fee schedules.\n\n"
            "Standard checking accounts require a minimum daily balance of $1,500 to waive the $12 monthly fee. "
            "If the balance falls below the minimum threshold at any point in the cycle, the maintenance fee applies.\n\n"
            "Accounts with zero transactions for 12 consecutive months are classified as inactive. "
            "Inactive accounts are subject to an inactivity fee of $5 per month until activity resumes or the account is closed."
        ),
    },
    {
        "doc_id": "dispute_resolution",
        "title": "Transaction Dispute and Chargeback Policy",
        "text": (
            "Consumer transaction dispute guidelines and resolution timelines.\n\n"
            "Cardholders must file transaction disputes within 60 calendar days of the statement date on which the charge appeared. "
            "Disputes filed after 60 calendar days cannot be processed.\n\n"
            "Provisional credit is granted within 10 business days for eligible claims while the investigation is underway. "
            "The merchant has 30 business days to provide evidence defending the original charge."
        ),
    },
]


def get_default_vector_store() -> ParentChildVectorStore:
    from pathlib import Path

    vec_db_path = os.getenv("VECTOR_DB_PATH", ":memory:")
    store = ParentChildVectorStore(vec_db_path=vec_db_path)
    for policy in DEFAULT_POLICIES:
        store.add_document(
            doc_id=policy["doc_id"],
            title=policy["title"],
            text=policy["text"],
        )

    base_dir = Path(__file__).resolve().parent.parent.parent
    policies_dir = base_dir / "data" / "policies"
    if policies_dir.exists():
        from app.rag.ingest import ingest_policies_directory

        ingest_policies_directory(store, policies_dir)

    return store
