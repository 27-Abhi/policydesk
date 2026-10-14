import math
import re
from collections import Counter
from typing import Any, Optional

from nltk.corpus import stopwords

STOPWORDS = set(stopwords.words("english"))


def _stem(word: str) -> str:
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 3:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 2:
        return word[:-1]
    if word.endswith("ing") and len(word) > 4:
        return word[:-3]
    if word.endswith("ed") and len(word) > 3:
        return word[:-2]
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

    def add_document(self, doc_id: str, title: str, text: str) -> None:
        parent_texts = self._split_into_chunks(text, chunk_size=500)
        for p_idx, p_text in enumerate(parent_texts):
            parent_id = f"{doc_id}_p{p_idx}"
            self.parents[parent_id] = {
                "parent_id": parent_id,
                "doc_id": doc_id,
                "title": title,
                "text": p_text,
            }

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

    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        query_vec = dict(Counter(query_tokens))

        scored_children = []
        for child in self.children:
            score = _cosine_similarity(query_vec, child["vector"])
            if score > 0.0:
                scored_children.append((score, child))

        scored_children.sort(key=lambda x: x[0], reverse=True)

        seen_parents: set[str] = set()
        matched_parents: list[dict[str, Any]] = []

        for score, child in scored_children:
            parent_id = child["parent_id"]
            if parent_id not in seen_parents:
                seen_parents.add(parent_id)
                parent_info = dict(self.parents[parent_id])
                parent_info["score"] = score
                matched_parents.append(parent_info)
                if len(matched_parents) >= top_k:
                    break

        return matched_parents


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

