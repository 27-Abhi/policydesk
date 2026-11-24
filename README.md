# PolicyDesk

**High-assurance, stateful financial support engine with Human-in-the-Loop (HITL) compliance orchestration, Agentic Corrective RAG (CRAG), and strict dual-port ingress isolation.**

---

## Overview

Automated financial support systems often fail when customer requests require compliance oversight (disputes, fraud reporting), multi-turn identity verification, or strict factual grounding over regulatory policy documents.

PolicyDesk solves this by orchestrating support workflows with **LangGraph** cyclic state machines and **FastAPI**, isolating public customer traffic from internal compliance tooling while maintaining deterministic state transitions and bounded Corrective RAG verification.

```
                    ┌────────────────────────────────────────────────────────┐
                    │                   PolicyDesk Gateway                   │
                    │                                                        │
[Customer Client] ──┼─► Port 8000: Customer Portal (customer_v1_router)      │
                    │   • Interactive Chat, Live Streaming & 2FA UI          │
                    │   • Architecture Explainer & Recorder (/explainer)     │
                    │   • POST /v1/chat/completions (JSON & SSE Streaming)   │
                    │   • GET  /v1/disputes/{thread_id}/events (Event Bus)   │
                    │                      │                                 │
                    │                      ▼                                 │
                    │         ┌────────────────────────┐                     │
                    │         │  LangGraph StateGraph  │                     │
                    │         │  (15 Nodes / Bounded)  │                     │
                    │         │  ────────────────────  │                     │
                    │         │  • Prompt Guardrails   │                     │
                    │         │  • Hybrid BM25 + Vec   │                     │
                    │         │  • CRAG Rewrite & Check│                     │
                    │         │  • 2FA Identity Engine │                     │
                    │         │  • HITL Breakpoints    │                     │
                    │         └───────────┬────────────┘                     │
                    │                     │                                  │
                    │         ┌───────────┴────────────┐                     │
                    │         ▼                        ▼                     │
                    │ ┌────────────────┐     ┌───────────────────┐           │
                    │ │ ThreadSafe     │     │   PostgreSQL 16   │           │
                    │ │ SqliteSaver    │     │   (psycopg v3 +   │           │
                    │ │ (WAL Mode) +   │     │   FOR UPDATE 2FA) │           │
                    │ │ sqlite-vec     │     │ Accounts/Disputes │           │
                    │ └────────────────┘     └───────────────────┘           │
                    │         ▲                        ▲                     │
                    │         └───────────┬────────────┘                     │
                    │                     │                                  │
[Internal Staff]  ──┼─► Port 8001: Compliance Operations (admin_v1_router)   │
                    │   • Dispute Case Review Board (Approve / Reject)       │
                    │   • Escalation Ticket Queue (SLA Triage)               │
                    │   • CRAG Observability, Citations & Latency Inspector  │
                    │   • Optional X-Admin-Key / Bearer Auth Enforcement     │
                    └────────────────────────────────────────────────────────┘
```

---

## Key Capabilities

### 1. Strict Dual-Port Ingress Isolation
- **Public Customer Ingress (`0.0.0.0:8000`)**: Mounts strictly `customer_v1_router` (`POST /v1/chat/completions`, `GET /v1/disputes/{thread_id}/events`, `GET /v1/policies`), serving the customer UI and `/explainer`. Compliance review, ingestion, and debug endpoints return `404` on Port 8000.
- **Internal Compliance Ingress (`0.0.0.0:8001`)**: Mounts `admin_v1_router`, `/v1/disputes`, and `/v1/escalations`, protected by optional `ADMIN_API_KEY` header/bearer authentication.
- **Configurable CORS**: Reads `CORS_ALLOWED_ORIGINS` (defaulting to localhost ports `8000` and `8001`) rather than wildcard origins.
- **Unified Process Runtime**: Both Uvicorn servers run concurrently under `asyncio.gather` within a single process ([`app/serve.py`](app/serve.py)), sharing core banking state and the in-memory `DisputeEventBus` without polling overhead.

### 2. Human-in-the-Loop (HITL) Dispute Resolution & Event-Driven SSE
- Disputed transactions persist to relational storage (`record_dispute`) and trigger a stateful pause via LangGraph's `interrupt()`, checkpointing execution to `ThreadSafeSqliteSaver` in WAL mode.
- Compliance reviewers adjudicate cases on Port 8001 (`POST /v1/disputes/{thread_id}/review`).
- Execution resumes via `Command(resume=...)` and immediately pushes a `dispute_resolved` event via `DisputeEventBus` ([`app/events.py`](app/events.py)) to any active `GET /v1/disputes/{thread_id}/events` SSE stream.

### 3. Agentic Hybrid Corrective RAG (CRAG)
- **Structure-Aware Ingestion** ([`app/rag/ingest.py`](app/rag/ingest.py)): Strips PDF running headers/page footers, preserves section headings and page numbers for inline citations, and sanitizes upload filenames against path traversal.
- **Hybrid Retrieval + RRF Fusion** ([`app/rag/store.py`](app/rag/store.py), [`app/rag/embeddings.py`](app/rag/embeddings.py)): Combines Okapi BM25 (`k1=1.5, b=0.75`) over stemmed unigrams/bigrams and typo-resolved tokens (`difflib`) with dense semantic vectors (`sqlite-vec` + local 256-dim subword/concept embedder, or OpenAI/Ollama embeddings when `ENABLE_REMOTE_EMBEDDINGS=true`), fused via Reciprocal Rank Fusion (RRF).
- **Stage-2 Cross-Feature Reranker** ([`app/rag/reranker.py`](app/rag/reranker.py)): Re-scores parent passages using dense alignment, IDF coverage, bigram overlap, section alignment, and optional LLM verification (`ENABLE_LLM_RERANK=true`).
- **Bounded CRAG Cycles** ([`app/graph/rag_nodes.py`](app/graph/rag_nodes.py)):
  - `grade_documents -> rewrite_query -> retrieve_parent_chunks` (bounded by `MAX_RETRIEVAL_ATTEMPTS = 2`).
  - `generate_answer -> verify_grounding` (checks numeric/currency/percentage grounding against retrieved clauses; bounded by `MAX_GENERATION_ATTEMPTS = 2`), failing closed to `abstain_policy_answer`.

### 4. Prompt-Injection Guardrails & Multi-Turn 2FA Identity Engine
- **Input Sanitization** ([`app/graph/nodes.py`](app/graph/nodes.py)): Enforces control-char stripping, `MAX_INPUT_LENGTH = 2000`, and prompt-injection/override detection that short-circuits malicious requests to safe refusal.
- **2FA Security** ([`app/tools/bank_db.py`](app/tools/bank_db.py)): Dynamic 6-digit OTPs (300s TTL, never logged in plaintext), RFC 6238 TOTP (`pyotp`), and 3-attempt brute-force lockout using `RLock` in memory and atomic `SELECT ... FOR UPDATE` row-level locking in `PostgresBankDatabase`.

### 5. Pluggable Multi-Provider LLM Client & Live Token Streaming
- Supports Anthropic Claude, OpenAI (and OpenAI-compatible endpoints), local Ollama (`gemma3:1b` / `qwen3:0.6b`), and a zero-cost `DeterministicMockClient` ([`app/llm/client.py`](app/llm/client.py)).
- Non-blocking execution via `anyio.to_thread.run_sync` and real live token streaming for `stream=True` policy completions with post-stream numeric grounding verification.

### 6. Observability & Quantitative Evaluation
- **Telemetry** ([`app/observability.py`](app/observability.py)): Per-node execution latency tracking, OpenTelemetry spans, token usage calculation in `/v1/chat/completions`, and full CRAG/citation debug inspection at `/v1/chat/debug`.
- **Automated Retrieval Benchmark** ([`scripts/evaluate_rag.py`](scripts/evaluate_rag.py)): Evaluates 170 golden queries (`evals/golden_benchmark.json`) across Hit@1, Hit@3, MRR, and out-of-scope specificity.
- **Agentic E2E Benchmark** ([`scripts/evaluate_agentic.py`](scripts/evaluate_agentic.py)) & **DeepEval Suite** ([`evals/test_deepeval.py`](evals/test_deepeval.py)).

---

## State Machine Architecture

```
                          ┌─────────────┐
                          │    START    │
                          └──────┬──────┘
                                 │
                                 ▼
                          ┌─────────────┐
                          │  sanitize   │───[injection/error]──► out_of_scope_placeholder ──► END
                          └──────┬──────┘
                                 │
                                 ▼
                       ┌───────────────────┐
                       │  classify_intent  │
                       └─────────┬─────────┘
                                 │
        ┌───────────────┬────────┼───────────────┬────────────────┐
        ▼               ▼        ▼               ▼                ▼
┌───────────────┐┌────────────┐┌────────────┐┌───────────────┐┌───────────────┐
│policy_question││ acct_lookup││  dispute   ││ human_request ││ out_of_scope  │
└───────┬───────┘└─────┬──────┘└─────┬──────┘└───────┬───────┘└───────┬───────┘
        │              │             │               │                │
        ▼              ▼             ▼               ▼                ▼
┌───────────────┐┌────────────┐┌────────────┐┌───────────────┐┌───────────────┐
│ CRAG Pipeline ││ 2FA Engine ││HITL Pause /││Support Ticket ││ Safe Refusal  │
│Retrieve-Grade-││State-Driven││interrupt() ││SLA Triage Tier││  Fallback     │
│Rewrite(<=2)-  ││Verification││& Resumption││  Assignment   ││               │
│Rerank-Gen-    ││(FOR UPDATE)││+ Event Bus ││+ PG Persist   ││               │
│Verify(<=2)    ││            ││            ││               ││               │
└───────┬───────┘└─────┬──────┘└─────┬──────┘└───────┬───────┘└───────┬───────┘
        │              │             │               │                │
        └──────────────┴────────┬────┴───────────────┴────────────────┘
                                │
                                ▼
                          ┌─────────────┐
                          │     END     │
                          └─────────────┘
```

---

## Getting Started

### Local Development

```bash
git clone https://github.com/27-Abhi/policydesk.git
cd policydesk

python -m venv .venv
source .venv/bin/activate       # On Linux/macOS (.venv\Scripts\Activate.ps1 on Windows)
pip install -e ".[dev]"

cp .env.example .env
python app/serve.py
```

### Docker Compose (EC2 Free Tier Ready)

```bash
docker compose up -d --build
```

Access points:
- **Customer Chat Portal**: [`http://localhost:8000`](http://localhost:8000)
- **Animated Architecture Explainer**: [`http://localhost:8000/explainer`](http://localhost:8000/explainer)
- **Compliance Operations Dashboard**: [`http://localhost:8001`](http://localhost:8001)

---

## Verification & Benchmarks

```bash
# Run full automated pytest suite
pytest -v

# Run 170-query offline Hybrid RAG retrieval benchmark
python scripts/evaluate_rag.py

# Run end-to-end Agentic routing, HITL, 2FA, and injection evaluation
python scripts/evaluate_agentic.py
```

---

## Technical Documentation
- [Technical Interview Dossier](docs/INTERVIEW_GUIDE.md)
- [Know By Heart Cheat Sheet](docs/KNOW_BY_HEART.md)
- [Agentic AI Interview Guide](docs/AGENTIC_AI_INTERVIEW_GUIDE.md)
- [AWS EC2 Free Tier Deployment Guide](docs/DEPLOYMENT_EC2.md)
