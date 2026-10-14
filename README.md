# PolicyDesk

**High-assurance, stateful financial support engine with Human-in-the-Loop (HITL) compliance orchestration, hierarchical RAG, and network ingress isolation.**

---

## Overview

Automated financial support systems often fail when customer requests require compliance oversight (disputes, fraud reporting) or sensitive multi-turn identity verification. 

PolicyDesk solves this by orchestrating support workflows with **LangGraph** state machines and **FastAPI**, isolating public customer traffic from internal compliance tooling while maintaining unified, deterministic state transitions.

```
                    ┌────────────────────────────────────────────────────────┐
                    │                   PolicyDesk Gateway                   │
                    │                                                        │
[Customer Client] ──┼─► Port 8000: Customer Portal                           │
                    │   • Interactive Chat & 2FA Challenge UI                │
                    │   • POST /v1/chat/completions (SSE Stream & JSON)      │
                    │   • GET  /v1/disputes/{thread_id}/events (SSE Push)    │
                    │                      │                                 │
                    │                      ▼                                 │
                    │         ┌────────────────────────┐                     │
                    │         │  LangGraph StateGraph  │                     │
                    │         │  ────────────────────  │                     │
                    │         │  • Typed State Machine │                     │
                    │         │  • Parent-Child RAG    │                     │
                    │         │  • 2FA Identity Engine │                     │
                    │         │  • HITL Breakpoints    │                     │
                    │         └───────────┬────────────┘                     │
                    │                     │                                  │
                    │                     ▼                                  │
                    │         ┌────────────────────────┐                     │
                    │         │  MemorySaver Checkpoint│                     │
                    │         └───────────▲────────────┘                     │
                    │                     │                                  │
[Internal Staff]  ──┼─► Port 8001: Compliance Operations                     │
                    │   • Dispute Case Review Board (Approve / Reject)       │
                    │   • Escalation Ticket Queue (SLA Triage)               │
                    │   • RAG Observability & Chunk Inspector                │
                    │   • POST /v1/disputes/{thread_id}/review               │
                    └────────────────────────────────────────────────────────┘
```

---

## Key Capabilities

### 1. Dual-Port Ingress Isolation
- **Public Customer Ingress (`0.0.0.0:8000`)**: Serves the customer-facing web interface and OpenAI-compatible `/v1/chat/completions` API.
- **Internal Compliance Ingress (`0.0.0.0:8001`)**: Serves the compliance dashboard, ticket triage queue, and dispute decision APIs.
- **Unified Process Runtime**: Both Uvicorn servers run concurrently under `asyncio.gather` within a single process ([`app/serve.py`](app/serve.py)), sharing an in-memory checkpointer and core banking state without external synchronization overhead.

### 2. Human-in-the-Loop (HITL) Dispute Resolution
- Disputed transactions trigger a stateful pause in the graph via LangGraph's native `interrupt()`, persisting thread execution to the checkpointer.
- Compliance reviewers inspect dispute evidence on Port 8001 and submit an approval or rejection decision.
- Execution seamlessly resumes via `Command(resume=...)`, notifying the customer client and updating dispute records in real time.

### 3. Server-Sent Events (SSE) Push
- Rather than requiring inefficient browser polling, Port 8000 exposes `GET /v1/disputes/{thread_id}/events`.
- When an investigator acts on Port 8001, the event is immediately pushed across the active HTTP/1.1 stream (`text/event-stream`), resolving the customer chat card instantly.

### 4. Hierarchical Parent-Child RAG
- Policy PDF documents are ingested into a two-tier vector memory store ([`app/rag/store.py`](app/rag/store.py)).
- **Chunking Strategy**: 500-character paragraph parent chunks are mapped to 160-character sentence child chunks.
- **Retrieval Flow**: Search runs cosine vector similarity against child chunks to locate granular facts, then returns full, cohesive parent clauses to eliminate fragmented context.
- **Relevance Grading**: Query-to-clause similarity is scored prior to generation; irrelevant queries cleanly fall back without hallucination.

### 5. Multi-Turn 2FA Security Engine
- Authenticated account inquiries require phone-based verification before sensitive balances or transaction logs are exposed.
- Implements RFC 6238 TOTP Authenticator apps (`pyotp`) and cryptographically randomized SMS tokens.
- **Defense in Depth**: Enforces a strict 3-attempt brute-force lockout before any master bypass checks are evaluated.

---

## State Machine Architecture

PolicyDesk routes every incoming request through a sanitized deterministic intent router:

```
                          ┌─────────────┐
                          │    START    │
                          └──────┬──────┘
                                 │
                                 ▼
                          ┌─────────────┐
                          │  sanitize   │
                          └──────┬──────┘
                                 │
                                 ▼
                       ┌───────────────────┐
                       │  classify_intent  │
                       └─────────┬─────────┘
                                 │
        ┌───────────────┬────────┼───────────────┬────────────────┐
        │               │        │               │                │
        ▼               ▼        ▼               ▼                ▼
┌───────────────┐┌────────────┐┌────────────┐┌───────────────┐┌───────────────┐
│policy_question││ acct_lookup││  dispute   ││ human_request ││ out_of_scope  │
└───────┬───────┘└─────┬──────┘└─────┬──────┘└───────┬───────┘└───────┬───────┘
        │              │             │               │                │
        ▼              ▼             ▼               ▼                ▼
┌───────────────┐┌────────────┐┌────────────┐┌───────────────┐┌───────────────┐
│4-Step RAG Flow││ 2FA Engine ││HITL Pause /││Support Ticket ││ Safe Refusal  │
│Retrieve-Grade-││State-Driven││interrupt() ││SLA Triage Tier││  Fallback     │
│Rerank-Generate││Verification││& Resumption││  Assignment   ││               │
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

## Repository Structure

```
policydesk/
├── app/
│   ├── serve.py              # Dual-port concurrent gateway runner (8000 & 8001)
│   ├── customer_app.py       # Port 8000 FastAPI customer portal
│   ├── admin_app.py          # Port 8001 FastAPI compliance operations portal
│   ├── main.py               # Standalone FastAPI service & CLI runner
│   ├── static/
│   │   ├── customer.html     # Customer chat & 2FA interactive interface
│   │   └── compliance.html   # Compliance investigation dashboard
│   ├── api/
│   │   ├── schemas.py        # Pydantic schemas (Chat completions, disputes)
│   │   └── v1/
│   │       └── router.py     # V1 endpoints (Chat, dispute review, SSE events)
│   ├── graph/
│   │   ├── state.py          # Intent enum and PolicyDeskState TypedDict
│   │   ├── graph.py          # StateGraph builder and MemorySaver compilation
│   │   ├── nodes.py          # Sanitize, intent classification, fallback
│   │   ├── rag_nodes.py      # Retrieve, grade, rerank, and generate
│   │   ├── dispute_nodes.py  # 3-node HITL dispute pipeline with interrupt()
│   │   ├── account_nodes.py  # Multi-turn 2FA account lookup
│   │   ├── escalation_nodes.py # Customer Care ticketing & SLA prioritization
│   │   └── router.py         # Deterministic conditional edge routing
│   ├── rag/
│   │   ├── store.py          # ParentChildVectorStore & stopword stemmer
│   │   └── ingest.py         # PDF parser & chunking ingestion pipeline
│   └── tools/
│       └── bank_db.py        # Core banking mock DB & 2FA cryptographic engine
├── data/
│   └── policies/             # Ingested PDF documents (Deposit Policies)
├── tests/
│   ├── conftest.py           # Pytest test guards
│   ├── test_port_apps.py     # Integration tests for Port 8000 & 8001
│   ├── test_dispute.py       # HITL dispute pause, resume, and review API
│   ├── test_account_escalation.py # 2FA auth and customer escalation
│   ├── test_rag.py           # RAG retrieval and PDF ingestion
│   ├── test_api.py           # FastAPI completion and streaming endpoints
│   ├── test_router.py        # Routing and fallback edge cases
│   └── test_nodes.py         # Input sanitization and intent classifiers
└── pyproject.toml            # Project dependencies and configuration
```

---

## Getting Started

### Prerequisites
- Python 3.10+ (Python 3.11 recommended)
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/27-Abhi/policydesk.git
cd policydesk

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate       # On Linux/macOS
# .\.venv\Scripts\Activate.ps1  # On Windows PowerShell

# Install package in editable mode
pip install -e .
pip install -e ".[dev]"
```

### Running the Application

Launch both portals simultaneously using the concurrent gateway:

```bash
python -m app.serve
```

Once running:
- **Customer Portal**: [`http://localhost:8000`](http://localhost:8000)
- **Compliance Operations Dashboard**: [`http://localhost:8001`](http://localhost:8001)

---

## Running Verification Tests

PolicyDesk includes a comprehensive test suite across all subsystems:

```bash
# Run targeted integration tests
pytest tests/test_port_apps.py tests/test_dispute.py tests/test_account_escalation.py tests/test_rag.py tests/test_api.py
```

---

## API Reference

### Customer Gateway (Port 8000)

| Endpoint | Method | Description |
|---|---|---|
| `/` | `GET` | Interactive Customer Chat & 2FA Web Application |
| `/v1/chat/completions` | `POST` | OpenAI-compatible chat completion (Supports JSON & SSE Streaming) |
| `/v1/disputes/{thread_id}/events` | `GET` | Real-time Server-Sent Events stream for dispute status resolution |
| `/health` | `GET` | Service readiness probe |

### Compliance Gateway (Port 8001)

| Endpoint | Method | Description |
|---|---|---|
| `/` | `GET` | Staff Compliance Review Dashboard |
| `/v1/disputes` | `GET` | Query all active and archived dispute threads |
| `/v1/disputes/{thread_id}` | `GET` | Detailed metadata and evidence for a specific dispute thread |
| `/v1/disputes/{thread_id}/review` | `POST` | Approve or reject a paused dispute, resuming the graph |
| `/v1/escalations` | `GET` | List prioritized Customer Care escalation tickets |
| `/v1/rag/inspect` | `POST` | Observability endpoint to test retrieval and grading against policy chunks |
| `/health` | `GET` | Compliance gateway readiness probe |

---

## Technical Specifications & Invariants

- **Zero-Dependency Vector Store**: Document similarity uses an in-memory TF cosine vectorizer with a pre-compiled English stopword set, removing heavy runtime dependencies.
- **Fail-Safe Intent Routing**: Unrecognized or malicious inputs fall back deterministically to `out_of_scope_placeholder`.
- **Checkpoint Isolation**: Sessions are isolated by `thread_id` headers (`X-Thread-ID`), guaranteeing zero state crosstalk between customer interactions.
