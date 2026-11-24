# PolicyDesk Progress Book

## 1. Document Purpose

This document records the current technical state of the PolicyDesk project so contributors and agents have an accurate map of implemented subsystems, constraints, and remaining operational work.

---

## 2. Project Description

PolicyDesk is an escalation-aware, stateful financial support system orchestrated in LangGraph across five routes:
1. Policy question (Bounded Hybrid Corrective RAG)
2. Account lookup (Multi-turn 2FA verified lookup)
3. Dispute action (HITL `interrupt()` + `Command(resume=...)`)
4. Human request (Customer Care SLA triage)
5. Out-of-scope / safety refusal

The system uses synthetic banking records and public/sample bank deposit policies.

---

## 3. Mandatory Engineering Rules

1. **Untrusted Input**: Sanitize, bound length, strip control chars, and block prompt-injection attempts before classification.
2. **Safe Fallback**: Route invalid, injected, or unrecognized intents to `out_of_scope_placeholder`.
3. **No Temporal Labels**: Never reference learning timelines or schedule markers in code comments or docstrings.
4. **Partial State Updates**: Nodes return only modified state keys.
5. **Bounded Loops**: CRAG retrieval retries (`MAX_RETRIEVAL_ATTEMPTS = 2`) and generation retries (`MAX_GENERATION_ATTEMPTS = 2`) are strictly bounded.
6. **Strict Ingress Isolation**: Customer Port `8000` mounts only `customer_v1_router`; compliance mutations and RAG inspection are restricted to Port `8001` (`admin_v1_router`).

---

## 4. Current System Status

### 4.1 Implemented Subsystems

1. **15-Node Agentic Workflow (`app/graph/`)**:
   - `sanitize -> [route_after_sanitize] -> classify_intent -> [route_by_intent]`
   - Bounded Corrective RAG pipeline: `retrieve_parent_chunks -> grade_documents -> [rewrite_query loop <= 2] -> rerank_documents -> generate_answer -> verify_grounding -> [retry <= 2 / abstain_policy_answer]`
   - Dispute HITL pipeline: `extract_dispute_details -> require_human_approval (interrupt) -> finalize_dispute`
   - 2FA Account Lookup (`handle_account_lookup`) and Customer Care Escalation (`handle_human_escalation`)
   - Thread-safe `ThreadSafeSqliteSaver` WAL checkpointer (`data/checkpoints.db`).

2. **Hybrid Production RAG (`app/rag/`)**:
   - `app/rag/embeddings.py`: `LocalSemanticEmbedder`, `OpenAIEmbeddingProvider`, `OllamaEmbeddingProvider`, and `SqliteVecIndex` (`sqlite-vec`).
   - `app/rag/ingest.py`: PDF header/footer cleaning, page & section extraction, filename sanitization.
   - `app/rag/store.py`: Parent-child store combining Okapi BM25 (`k1=1.5, b=0.75`), dense vector search, RRF fusion, `difflib` typo tolerance, and out-of-domain product filtering.
   - `app/rag/reranker.py`: Stage-2 cross-feature passage reranker with optional LLM verification.

3. **Dual-Port FastAPI Gateway & Real-Time SSE (`app/customer_app.py`, `app/admin_app.py`, `app/events.py`)**:
   - Strict router separation (`customer_v1_router` on Port 8000, `admin_v1_router` on Port 8001 with optional `ADMIN_API_KEY`).
   - Non-blocking graph execution (`anyio.to_thread.run_sync`), live LLM token streaming, and `DisputeEventBus` real-time SSE resolution push.
   - Interactive web UIs (`customer.html`, `compliance.html`, `explainer.html`).

4. **Relational Persistence & Observability (`app/tools/bank_db.py`, `app/db/`, `app/observability.py`)**:
   - PostgreSQL 16 schema (`accounts`, `transactions`, `disputes`, `escalations`) with `SELECT ... FOR UPDATE` row-level 2FA locking and `record_dispute` / `record_escalation` persistence.
   - Per-node latency metrics, token usage reporting, OpenTelemetry span tracing, and `/v1/chat/debug`.

5. **Evaluation Harnesses (`scripts/`, `evals/`)**:
   - 170-query retrieval benchmark (`scripts/evaluate_rag.py`)
   - End-to-end Agentic evaluation (`scripts/evaluate_agentic.py`)
   - DeepEval & Ragas LLM-as-a-judge suites (`evals/test_deepeval.py`, `evals/run_deepeval_comprehensive.py`, `scripts/evaluate_ragas.py`).

---

## 5. Remaining Operational Work

1. Live AWS EC2 Free Tier deployment and `docker compose` smoke testing against live PostgreSQL 16.
2. Expanding DeepEval/Ragas test cases beyond the initial 10-case report when live API quota is active.
3. Optional migration from single-process `DisputeEventBus` + `SqliteSaver` to Redis Pub/Sub + `AsyncPostgresSaver` if scaling horizontally past a single node.
