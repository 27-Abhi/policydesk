"""Pluggable embedding providers and sqlite-vec persistent vector index."""

import hashlib
import logging
import math
import os
import re
import sqlite3
import struct
import threading
from typing import Any, Optional

import httpx

logger = logging.getLogger("policydesk.rag.embeddings")

LOCAL_EMBEDDING_DIM = 256

# Canonical domain synonym clusters mapped onto shared semantic dimensions
_CONCEPT_CLUSTERS: tuple[tuple[str, ...], ...] = (
    ("online", "digital", "digitally", "branchless", "e-banking"),
    ("fd", "fixed", "term", "reinvestment", "maturity", "tenors", "tenor", "tenure", "cumulative"),
    ("premature", "early", "before", "break", "foreclose", "foreclosure", "prior"),
    ("dormant", "inoperative", "inactive", "unclaimed", "deaf", "udgam"),
    ("minor", "child", "children", "kid", "juvenile", "guardian", "18", "majority", "major"),
    ("nominee", "nomination", "nominate", "beneficiary", "successor", "heir", "legal"),
    ("person", "individual", "individuals", "human", "customer", "depositor", "holder"),
    ("locker", "lockers", "safe", "vault", "custody"),
    ("deceased", "death", "die", "dies", "died", "expired", "demise", "missing", "presumed"),
    ("fee", "fees", "charge", "charges", "penalty", "penal", "cost", "levy", "levied", "amb"),
    ("wire", "wires", "remittance", "transfer", "transfers", "swift", "neft", "rtgs", "imps"),
    ("tax", "tds", "15g", "15h", "exemption", "deduction", "deducted", "pan"),
    ("dicgc", "insurance", "insured", "cover", "coverage", "protection", "guaranteed"),
    ("dispute", "chargeback", "unauthorized", "fraud", "provisional", "rebuttal"),
    ("senior", "elderly", "pensioner", "pensioners", "retired", "age", "60"),
    ("nri", "nre", "nro", "fcnr", "rfc", "foreign", "overseas", "repatriation"),
    ("joint", "survivor", "either", "former", "mandate", "anyone"),
    ("kyc", "identity", "address", "verification", "diligence", "document", "documents"),
    ("passbook", "statement", "cheque", "checkbook", "check"),
    ("illiterate", "thumb", "blind", "visual", "visually", "impaired", "autism", "cerebral", "palsy", "disability", "transgender"),
    ("sweep", "auto", "renewal", "renew", "renewed", "maturity", "instructions"),
    ("loan", "advance", "overdraft", "pledge", "lien", "collateral"),
)

_TOKEN_TO_CONCEPT: dict[str, int] = {}
for _cid, _group in enumerate(_CONCEPT_CLUSTERS):
    for _term in _group:
        _TOKEN_TO_CONCEPT[_term] = _cid


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-12:
        return vec
    return [v / norm for v in vec]


def cosine_similarity_dense(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two dense float vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 <= 1e-12 or n2 <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, dot / (n1 * n2)))


def pack_f32(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_f32(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


class BaseEmbeddingProvider:
    """Interface for dense text embedding generation."""

    dim: int = LOCAL_EMBEDDING_DIM

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class LocalSemanticEmbedder(BaseEmbeddingProvider):
    """Zero-dependency deterministic dense semantic embedder using feature hashing, char n-grams, and concept axes."""

    def __init__(self, dim: int = LOCAL_EMBEDDING_DIM) -> None:
        self.dim = dim

    @staticmethod
    def _hash_feature(feature: str, buckets: int) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        val = int.from_bytes(digest[:4], "little")
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        return val % buckets, sign

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        if not text:
            return vec

        clean = text.lower()
        words = re.findall(r"\b[a-z0-9_]+\b", clean)
        if not words:
            return vec

        # Reserve trailing dimensions for canonical banking concept alignment
        concept_slots = len(_CONCEPT_CLUSTERS)
        hash_buckets = max(64, self.dim - concept_slots)

        # 1. Word unigrams and bigrams
        for i, w in enumerate(words):
            if len(w) <= 1:
                continue
            idx, sign = self._hash_feature(f"w:{w}", hash_buckets)
            vec[idx] += sign * 1.2

            # Concept alignment axis
            cid = _TOKEN_TO_CONCEPT.get(w)
            if cid is not None:
                vec[hash_buckets + cid] += 2.2

            if i + 1 < len(words):
                nxt = words[i + 1]
                if len(nxt) > 1:
                    b_idx, b_sign = self._hash_feature(f"b:{w}_{nxt}", hash_buckets)
                    vec[b_idx] += b_sign * 1.4

        # 2. Character 3-grams and 4-grams for typo/morphological resilience
        for w in words:
            if len(w) < 3:
                continue
            padded = f"^{w}$"
            for n in (3, 4):
                if len(padded) < n:
                    continue
                for j in range(len(padded) - n + 1):
                    gram = padded[j : j + n]
                    g_idx, g_sign = self._hash_feature(f"c{n}:{gram}", hash_buckets)
                    vec[g_idx] += g_sign * 0.45

        return _l2_normalize(vec)


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """OpenAI-compatible /embeddings API provider with deterministic local fallback."""

    def __init__(self, api_key: str, model: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self.api_key = api_key
        self.model = model or os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self._fallback = LocalSemanticEmbedder()
        self.dim = int(os.getenv("EMBEDDING_DIM", "1536"))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        url = f"{self.base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "input": texts}
        try:
            with httpx.Client(timeout=20.0) as client:
                res = client.post(url, json=payload, headers=headers)
                res.raise_for_status()
                data = res.json()
                rows = sorted(data["data"], key=lambda item: item["index"])
                vectors = [_l2_normalize(list(row["embedding"])) for row in rows]
                if vectors:
                    self.dim = len(vectors[0])
                return vectors
        except Exception as exc:
            logger.warning("[RAG:EMBED:OPENAI] Embedding request failed (%s); using local semantic embedder.", exc)
            self.dim = self._fallback.dim
            return self._fallback.embed_batch(texts)

    def embed(self, text: str) -> list[float]:
        res = self.embed_batch([text])
        return res[0] if res else self._fallback.embed(text)


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    """Local Ollama /api/embeddings provider with deterministic local fallback."""

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        self._fallback = LocalSemanticEmbedder()
        self.dim = int(os.getenv("EMBEDDING_DIM", "768"))

    def embed(self, text: str) -> list[float]:
        url = f"{self.base_url}/api/embeddings"
        payload = {"model": self.model, "prompt": text}
        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(url, json=payload)
                res.raise_for_status()
                vec = list(res.json()["embedding"])
                self.dim = len(vec)
                return _l2_normalize(vec)
        except Exception as exc:
            logger.warning("[RAG:EMBED:OLLAMA] Embedding request failed (%s); using local semantic embedder.", exc)
            self.dim = self._fallback.dim
            return self._fallback.embed(text)


def get_embedding_provider() -> BaseEmbeddingProvider:
    """Resolve embedding provider; uses local semantic vectors unless remote embeddings are enabled."""
    enable_remote = os.getenv("ENABLE_REMOTE_EMBEDDINGS", "false").strip().lower() in ("1", "true", "yes")
    if enable_remote:
        openai_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
        if openai_key and openai_key.strip():
            return OpenAIEmbeddingProvider(api_key=openai_key.strip())
        ollama_url = os.getenv("OLLAMA_BASE_URL")
        if ollama_url and ollama_url.strip():
            return OllamaEmbeddingProvider(base_url=ollama_url.strip())
    return LocalSemanticEmbedder()


class SqliteVecIndex:
    """Thread-safe vector index backed by sqlite-vec when available, with Python fallback."""

    def __init__(self, db_path: str = ":memory:", dim: int = LOCAL_EMBEDDING_DIM) -> None:
        self.db_path = db_path
        self.dim = dim
        self._lock = threading.RLock()
        self._vec_enabled = False
        self._vectors: dict[str, list[float]] = {}
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        if self.db_path != ":memory:":
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                import sqlite_vec

                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                self._vec_enabled = True
            except Exception:
                self._vec_enabled = False

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_vectors (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    embedding BLOB NOT NULL
                )
                """
            )
            conn.commit()
            self._conn = conn
        except Exception as exc:
            logger.warning("[RAG:VEC] SQLite initialization fell back to in-memory map: %s", exc)
            self._conn = None
            self._vec_enabled = False

    def upsert(self, chunk_id: str, doc_id: str, vector: list[float]) -> None:
        with self._lock:
            self._vectors[chunk_id] = vector
            if self._conn is not None:
                try:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO chunk_vectors (chunk_id, doc_id, dim, embedding) VALUES (?, ?, ?, ?)",
                        (chunk_id, doc_id, len(vector), pack_f32(vector)),
                    )
                    self._conn.commit()
                except Exception:
                    pass

    def delete_doc(self, doc_id: str) -> None:
        with self._lock:
            prefix = f"{doc_id}_p"
            to_del = [cid for cid in self._vectors if cid.startswith(prefix)]
            for cid in to_del:
                del self._vectors[cid]
            if self._conn is not None:
                try:
                    self._conn.execute("DELETE FROM chunk_vectors WHERE doc_id = ?", (doc_id,))
                    self._conn.commit()
                except Exception:
                    pass

    def search(self, query_vec: list[float], candidate_ids: Optional[set[str]] = None) -> dict[str, float]:
        """Return {chunk_id: cosine_similarity} via sqlite-vec distance SQL or Python fallback."""
        with self._lock:
            scores: dict[str, float] = {}
            if self._vec_enabled and self._conn is not None and self._vectors:
                try:
                    import sqlite_vec

                    q_blob = sqlite_vec.serialize_float32(query_vec)
                    rows = self._conn.execute(
                        "SELECT chunk_id, vec_distance_cosine(embedding, ?) AS dist FROM chunk_vectors WHERE dim = ?",
                        (q_blob, len(query_vec)),
                    ).fetchall()
                    for cid, dist in rows:
                        if candidate_ids is not None and cid not in candidate_ids:
                            continue
                        if dist is None:
                            continue
                        sim = max(0.0, min(1.0, 1.0 - float(dist)))
                        if sim > 0.0:
                            scores[cid] = sim
                    return scores
                except Exception:
                    pass

            for cid, vec in self._vectors.items():
                if candidate_ids is not None and cid not in candidate_ids:
                    continue
                if len(vec) != len(query_vec):
                    continue
                sim = cosine_similarity_dense(query_vec, vec)
                if sim > 0.0:
                    scores[cid] = sim
            return scores

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
