# PolicyDesk — Escalation-Aware Fintech Policy Agent

PolicyDesk is an escalation-aware fintech policy support agent built using LangGraph.

---

## 1. Architecture

```
START
  │
  ▼
sanitize
  │
  ▼
classify_intent
  │
  ├── [policy_question] ────────► policy_placeholder ───────► END
  ├── [account_lookup]  ────────► account_placeholder ──────► END
  ├── [dispute_action]  ────────► dispute_placeholder ──────► END
  ├── [human_request]   ────────► human_placeholder ────────► END
  └── [out_of_scope]    ────────► out_of_scope_placeholder ─► END
```

---

## 2. Project Structure

```
policydesk/
├── README.md
├── pyproject.toml
├── app/
│   ├── __init__.py
│   ├── main.py              # Application entrypoint
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── router.py    # V1 API router
│   └── graph/
│       ├── __init__.py
│       ├── state.py         # Intent enum & PolicyDeskState TypedDict
│       ├── nodes.py         # sanitize, classify_intent, placeholder nodes
│       ├── router.py        # route_by_intent with safe fallback
│       └── graph.py         # StateGraph assembly & build_graph()
└── tests/
    ├── test_nodes.py        # Node unit tests
    ├── test_router.py       # Router unit tests (valid & fallback)
    └── test_graph.py        # End-to-end graph routing tests
```

---

## 3. Running Tests & Demo

### Run Tests
```bash
pytest
```

### Run Demo
```bash
python -m app.main
```
