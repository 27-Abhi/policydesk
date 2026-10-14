# PolicyDesk Progress Book

## 1. Document Purpose

This document records the current technical progress of the PolicyDesk project. Any software agent can read this document to understand the codebase state, system constraints, and next tasks.

---

## 2. Project Description

PolicyDesk is an escalation-aware financial support system. It processes user requests using stateful workflows in LangGraph. The system routes requests to five target paths:
1. Policy question
2. Account lookup
3. Dispute action
4. Human request
5. Out-of-scope request

The system uses synthetic account data and modular bank policy documents. It does not use confidential customer data or production credentials.

---

## 3. Mandatory Engineering Rules

All agents must obey these rules:

1. **Untrusted Input**: Treat all user inputs as untrusted data. Sanitize text before processing.
2. **Safe Fallback**: If an intent is uncertain or invalid, route execution to `out_of_scope_placeholder`.
3. **No Temporal Labels**: Do not write comments or docstrings that reference learning schedules, timelines, or phase numbers (for example: do not write "Day 1" or "Phase 1").
4. **Partial State Updates**: Nodes must return only modified state keys. Nodes must not rebuild unchanged state values.
5. **Bounded Loops**: All future graph cycles must use strict limits. Do not make unbounded loops.
6. **No Arbitrary Destinations**: Model output must never directly select arbitrary code or unmapped graph routes.

---

## 4. Current System Status

### 4.1 Completed Components

1. **Foundation Graph**:
   - **Typed State**: `PolicyDeskState` defines `user_input`, `intent`, `route`, `errors`, `retrieved_docs`, `reranked_docs`, `relevance_score`, `is_relevant`, `answer`.
   - **Intent Schema**: `Intent` enum specifies five valid categories.
   - **Sanitization Node**: `sanitize()` validates string inputs and trims whitespace.
   - **Classification Node**: `classify_intent()` classifies intent deterministically with domain term recognition.
   - **Conditional Router**: `route_by_intent()` selects the next node with total fallback coverage.
   - **Graph Assembly**: `build_graph()` compiles sequential and conditional edges from `START` to `END`.

2. **Hierarchical RAG Pipeline & PDF Ingestion**:
   - **Parent-Child Vector Store**: `ParentChildVectorStore` maps searchable sentence-level child chunks to paragraph-level parent policy clauses with cosine similarity.
   - **PDF Ingestion Engine**: `app/rag/ingest.py` extracts text from policy PDFs and ingests `data/policies/Comprehensive_Deposit_Policy.pdf` (152 parent chunks, 389 child chunks).
   - **4-Step Validation Flow**: `retrieve_parent_chunks -> grade_documents -> rerank_documents -> generate_answer`.

3. **FastAPI OpenAI-Compatible Service Layer**:
   - **Schemas**: Standard OpenAI-compatible `ChatCompletionRequest` and `ChatCompletionResponse`.
   - **Endpoints**: `POST /v1/chat/completions` and `/api/v1/chat/completions` supporting synchronous JSON and SSE streaming (`stream=True`).
   - **Health Probe**: `GET /health` endpoint for readiness/liveness checks.
   - **Dependency Injection**: Singleton compiled graph managed via `Depends(get_graph)`.

### 4.2 Verification

- **Test Suite**: 53 unit and integration tests pass in `pytest`.
- **Test File Coverage**: `tests/test_nodes.py`, `tests/test_router.py`, `tests/test_graph.py`, `tests/test_rag.py`, `tests/test_api.py`.
- **Interactive Verification**: End-to-end execution verified against both built-in policy rules and real ingested PDF clauses.

---

## 5. Repository Structure

```text
policydesk/
├── README.md                # Project overview and run instructions
├── project.md               # Technical specification document
├── progress.md              # Current progress book (this document)
├── pyproject.toml           # Package configuration and dependencies
├── AGENTS.md                # Workspace coding instructions for agents
├── app/
│   ├── __init__.py          # Application package root
│   ├── main.py              # CLI demo runner
│   ├── api/
│   │   ├── __init__.py      # API package
│   │   └── v1/
│   │       ├── __init__.py  # V1 API package
│   │       └── router.py    # V1 router placeholder
│   └── graph/
│       ├── __init__.py      # Graph package
│       ├── state.py         # Intent enum and PolicyDeskState TypedDict
│       ├── nodes.py         # Node functions and placeholders
│       ├── router.py        # route_by_intent routing logic
│       └── graph.py         # StateGraph builder and compiler
├── data/
│   └── policies/            # Raw PDF/text documents (Comprehensive_Deposit_Policy.pdf, english.pdf)
└── tests/
    ├── test_nodes.py        # Tests for sanitize, classify, and placeholders
    ├── test_router.py       # Tests for router paths and fallbacks
    └── test_graph.py        # End-to-end graph execution tests
```

---

## 6. Execution Commands

### Run Unit Tests
```bash
pytest
```

### Run Demonstration Script
```bash
python -m app.main
```

---

## 7. Next Tasks

1. **RAG Ingestion and Chunking Design**:
   - Reason through the parsing and chunking strategy for policy documents.
   - Decide on parent/child representations and metadata schemas.
2. **Storage and Vector Database Layer**:
   - Evaluate options for chunk and embedding persistence.
3. **Retrieval and Grading**:
   - Dense + sparse retrieval and corrective loops.
