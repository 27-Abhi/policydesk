import difflib
import math
import re
from collections import Counter
from typing import Any, Optional

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
    return [_stem(w) for w in words if w not in STOPWORDS]



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


class ParentChildVectorStore:
    """In-memory vector store mapping searchable child chunks to full parent chunks."""

    def __init__(self) -> None:
        self.parents: dict[str, dict[str, Any]] = {}
        self.children: list[dict[str, Any]] = []
        self.df: Counter = Counter()
        self.doc_keywords: dict[str, set[str]] = {}

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
        return math.log((total - n + 0.5) / (n + 0.5) + 1.0) + 1.0

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
            else:
                new_children.append(child)
        self.children = new_children
        self.df += Counter()
        if doc_id in self.doc_keywords:
            del self.doc_keywords[doc_id]

    def add_document(self, doc_id: str, title: str, text: str) -> tuple[int, int]:
        self.remove_document(doc_id)

        # Extract dynamic corpus keywords from title and salient document vocabulary
        kw_set: set[str] = set()
        for w in re.findall(r"\b[a-zA-Z0-9_]{3,}\b", title.lower()):
            if w not in STOPWORDS:
                kw_set.add(w)
                kw_set.add(_stem(w))

        words = [w for w in re.findall(r"\b[a-zA-Z0-9_]{4,}\b", text.lower()) if w not in STOPWORDS]
        word_counts = Counter(words)
        for w, cnt in word_counts.items():
            if cnt >= 2:
                kw_set.add(w)
                kw_set.add(_stem(w))

        self.doc_keywords[doc_id] = kw_set

        parent_texts = self._split_into_chunks(text, chunk_size=500)
        num_parents = 0
        num_children = 0
        for p_idx, p_text in enumerate(parent_texts):
            parent_id = f"{doc_id}_p{p_idx}"
            self.parents[parent_id] = {
                "parent_id": parent_id,
                "doc_id": doc_id,
                "seq_idx": p_idx,
                "title": title,
                "text": p_text,
            }
            num_parents += 1

            child_texts = self._split_sentences(p_text, child_size=160)
            for c_idx, c_text in enumerate(child_texts):
                tokens = _tokenize(c_text)
                tf = dict(Counter(tokens))
                child_id = f"{parent_id}_c{c_idx}"
                self.children.append(
                    {
                        "child_id": child_id,
                        "parent_id": parent_id,
                        "text": c_text,
                        "vector": tf,
                    }
                )
                self.df.update(tf.keys())
                num_children += 1
        return num_parents, num_children

    def score_child(self, query_tokens: list[str], child: dict[str, Any]) -> float:
        if not query_tokens:
            return 0.0
        q_counts = Counter(query_tokens)
        c_counts = child["vector"]
        common = set(q_counts.keys()) & set(c_counts.keys())
        if not common:
            return 0.0

        dot = sum(q_counts[w] * c_counts[w] * (self.get_idf(w) ** 2) for w in common)
        norm1 = math.sqrt(sum((q_counts[w] * self.get_idf(w)) ** 2 for w in q_counts))
        norm2 = math.sqrt(sum((c_counts[w] * self.get_idf(w)) ** 2 for w in c_counts))
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        cosine = dot / (norm1 * norm2)
        coverage = len(common) / len(q_counts)
        return cosine * (0.5 + 0.5 * coverage)

    def resolve_tokens(self, tokens: list[str]) -> list[str]:
        resolved = []
        for tok in tokens:
            if tok in self.df:
                resolved.append(tok)
            elif len(tok) >= 5:
                matches = difflib.get_close_matches(tok, self.df.keys(), n=1, cutoff=0.8)
                resolved.append(matches[0] if matches else tok)
            else:
                resolved.append(tok)
        return resolved

    def retrieve(self, query: str, top_k: int = 3, expand_window: int = 1) -> list[dict[str, Any]]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        query_tokens = self.resolve_tokens(query_tokens)
        scored_children = []
        for child in self.children:
            score = self.score_child(query_tokens, child)
            if score > 0.0:
                scored_children.append((score, child))

        scored_children.sort(key=lambda x: x[0], reverse=True)

        seen_parents: set[str] = set()
        seen_texts: set[str] = set()
        matched_parents: list[dict[str, Any]] = []

        for score, child in scored_children:
            parent_id = child["parent_id"]
            if parent_id in seen_parents:
                continue

            parent_info = dict(self.parents[parent_id])
            norm_text = re.sub(r"\s+", " ", parent_info["text"]).strip().lower()
            if norm_text in seen_texts:
                continue

            seen_parents.add(parent_id)
            seen_texts.add(norm_text)
            parent_info["score"] = score

            # Surrounding chunk window expansion
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

    store = ParentChildVectorStore()
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

