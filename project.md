# PolicyDesk

## Escalation-Aware AI Policy Support Agent

PolicyDesk is a **stateful AI support agent for financial-service policy and account workflows**.

The project demonstrates how to build a production-oriented AI workflow using **LangGraph, RAG, tool calling, structured outputs, human-in-the-loop approval, checkpointing, FastAPI, PostgreSQL/pgvector, and AWS**.

The system is intentionally built around **synthetic financial-policy data**. It does not use any confidential ClaySys, customer, or production data.

---

# 1. Problem

A normal RAG chatbot can answer questions such as:

> "What is the policy for disputing a transaction?"

But real support workflows are more complicated.

A user may:

* Ask a policy question
* Ask for account information
* Request an action that changes data
* Ask for a human
* Ask something outside the system's scope
* Provide malicious or irrelevant instructions
* Ask a question where retrieved documents are insufficient

PolicyDesk handles these cases differently instead of treating everything as:

```text
User → Retrieve Documents → LLM → Answer
```

The system decides **what kind of request it received**, chooses an appropriate workflow, retrieves evidence when necessary, uses tools for account data, and requires **human approval before sensitive state-changing actions**.

---

# 2. Core Objective

Build an AI support system that can safely handle five categories of requests:

```text
                    ┌──────────────────────┐
                    │      User Request    │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │      Sanitize        │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   Intent Classifier  │
                    └──────────┬───────────┘
                               │
                    ┌──────────┴──────────┐
                    │   Conditional Route │
                    └──────────┬──────────┘
                               │
       ┌────────────┬──────────┼───────────┬──────────────┐
       ▼            ▼          ▼           ▼              ▼
   Policy       Account     Dispute      Human        Out of
  Question     Lookup      Action       Request       Scope
       │            │          │           │              │
       ▼            ▼          ▼           ▼              ▼
     RAG         Read Tool   Approval    Handoff       Refusal
       │                       │
       ▼                       ▼
   Verify Answer          Execute Action
       │                       │
       └───────────┬───────────┘
                   ▼
                  END
```

---

# 3. What Makes This Different From a Basic RAG Chatbot?

A basic RAG application generally looks like:

```text
Question
   ↓
Embedding
   ↓
Vector Search
   ↓
LLM
   ↓
Answer
```

PolicyDesk adds **workflow control and safety**:

```text
                 ┌───────────────┐
                 │ User Request  │
                 └───────┬───────┘
                         ↓
                    Classification
                         ↓
                    Conditional
                     Routing
                         │
        ┌────────────────┼────────────────┐
        ↓                ↓                ↓
       RAG              Tools            HITL
        ↓                ↓                ↓
     Verify           Validate        Approve
        ↓                ↓                ↓
        └────────────────┼────────────────┘
                         ↓
                      Response
```

The important distinction is that **the LLM does not independently decide everything**.

The application controls:

* Which workflow is executed
* Which tools are available
* Whether an action requires approval
* How many retrieval attempts are allowed
* What happens when evidence is insufficient
* What happens when a tool fails
* What happens when the LLM produces invalid output

---

# 4. Main User Flows

## 4.1 Policy Question

Example:

> "What is the policy for disputing a card transaction?"

Flow:

```text
User
 │
 ▼
Sanitize
 │
 ▼
Classify Intent
 │
 ▼
Policy Question
 │
 ▼
Retrieve Policy Documents
 │
 ▼
Grade Retrieved Documents
 │
 ├── Good ────────────────┐
 │                        │
 └── Poor                 │
       │                  │
       ▼                  │
   Rewrite Query          │
       │                  │
       ▼                  │
   Retrieve Again         │
       │                  │
       └──────────────────┘
                │
                ▼
          Draft Answer
                │
                ▼
        Grounding Check
                │
        ┌───────┴────────┐
        │                │
     Grounded        Not Grounded
        │                │
        ▼                ▼
     Answer          Escalate
```

The retrieval loop is **bounded**.

The system must not endlessly rewrite and retrieve.

---

# 4.2 Account Lookup

Example:

> "What is my current account balance?"

This is not primarily a RAG problem.

The system should use a tool:

```text
User
 │
 ▼
Classify
 │
 ▼
Account Lookup
 │
 ▼
Account Read Tool
 │
 ▼
Validate Result
 │
 ▼
Response
```

The project uses **synthetic account data**.

For example:

```text
account_id
customer_id
balance
status
recent_transactions
```

The purpose is to demonstrate the difference between:

```text
Knowledge retrieval
```

and

```text
Live application data retrieval
```

---

# 4.3 Dispute Action

Example:

> "I want to dispute a transaction."

Unlike an account lookup, this request can **change application state**.

Therefore it should not automatically execute.

```text
User
 │
 ▼
Classify
 │
 ▼
Dispute Action
 │
 ▼
Validate Request
 │
 ▼
Create Action Plan
 │
 ▼
┌───────────────────────────┐
│ Human Approval Required   │
└────────────┬──────────────┘
             │
          interrupt()
             │
       ┌─────┴─────┐
       │           │
    Approved     Rejected
       │           │
       ▼           ▼
 Execute Tool    Cancel
       │
       ▼
 Record Outcome
       │
       ▼
     Response
```

This is one of the key reasons for using LangGraph.

The graph can **pause execution and resume later**.

---

# 4.4 Human Request

Example:

> "I want to speak to a human."

The system should not attempt to answer the user indefinitely.

```text
User
 │
 ▼
Classify
 │
 ▼
Human Request
 │
 ▼
Handoff / Escalation
 │
 ▼
END
```

---

# 4.5 Out-of-Scope Request

Example:

> "Write me a Python game."

PolicyDesk should refuse or redirect instead of pretending to be a general-purpose assistant.

```text
User
 │
 ▼
Classify
 │
 ▼
Out of Scope
 │
 ▼
Safe Response
 │
 ▼
END
```

---

# 5. Complete Architecture

```text
                         ┌─────────────────┐
                         │      Client     │
                         └────────┬────────┘
                                  │
                                  ▼
                         ┌─────────────────┐
                         │     FastAPI     │
                         │   /chat         │
                         │   /approve      │
                         └────────┬────────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │       LangGraph          │
                    │                          │
                    │  Shared State            │
                    │                          │
                    │  sanitize                 │
                    │      ↓                   │
                    │  classify_intent         │
                    │      ↓                   │
                    │  conditional routing     │
                    └────────────┬─────────────┘
                                 │
          ┌──────────────────────┼───────────────────────┐
          │                      │                       │
          ▼                      ▼                       ▼
   ┌──────────────┐      ┌──────────────┐       ┌──────────────┐
   │     RAG      │      │    Tools     │       │     HITL     │
   │              │      │              │       │              │
   │ pgvector     │      │ Account Read │       │ interrupt()  │
   │ retrieval    │      │ Dispute      │       │ approval     │
   │ grading      │      │ Action       │       │ resume       │
   │ rewriting    │      │              │       │              │
   └──────┬───────┘      └──────┬───────┘       └──────┬───────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                                 ▼
                          ┌────────────────────────┐
                          │      LLM Provider      │
                          │ Bedrock (Prod) / Ollama│
                          └────────────────────────┘

                                 │
                                 ▼
                    ┌────────────────────────┐
                    │ PostgreSQL + pgvector  │
                    │                        │
                    │ Policy documents       │
                    │ Checkpoints             │
                    │ Synthetic data         │
                    └────────────────────────┘

                                 │
                                 ▼
                         ┌─────────────────┐
                         │   Observability │
                         │   CloudWatch    │
                         └─────────────────┘
```

---

# 6. LangGraph State

The graph operates around a shared state.

Conceptually:

```text
State
├── user_input
├── intent
├── query
├── retrieved_documents
├── retrieval_attempts
├── draft
├── citations
├── grounded
├── generation_attempts
├── pending_action
├── approval
├── outcome
├── errors
└── metrics
```

Nodes should generally return **only the fields they changed**.

For example:

```python
return {
    "intent": "policy_question"
}
```

rather than reconstructing the entire state.

### State Update Semantics and Reducers

* **Partial Updates**: Nodes return only changed keys; LangGraph automatically merges them into state.
* **Counters**: Bounded loop counters (`retrieval_attempts`, `generation_attempts`) increment explicitly within their respective nodes.
* **Collections**: Fields like `errors` can use reducers (e.g., `Annotated[list[str], operator.add]`) when cumulative history across nodes is required.

---

# 7. Corrective RAG

PolicyDesk does not simply retrieve documents once and trust the result.

The intended flow is:

```text
Query
  ↓
Retrieve
  ↓
Grade Results
  │
  ├── Relevant ────────→ Generate
  │
  └── Poor
       ↓
   Rewrite Query
       ↓
    Retrieve
       ↓
     Grade
       │
       ├── Relevant → Generate
       │
       └── Still Poor → Escalate
```

The retry/rewrite loop has a strict limit.

For example:

```text
Maximum retrieval attempts = 2
```

This prevents:

```text
retrieve → rewrite → retrieve → rewrite → ...
```

from becoming an infinite loop.

---

# 8. Grounding Verification

After generating an answer, PolicyDesk checks whether the response is actually supported by the retrieved policy documents.

Conceptually:

```text
Retrieved Evidence
        +
Generated Answer
        ↓
Grounding Verification
        │
   ┌────┴────┐
   │         │
Supported  Unsupported
   │         │
   ▼         ▼
Answer    Escalate /
           Refuse
```

The goal is to distinguish:

```text
"I don't know"
```

from:

```text
"I found evidence, and the evidence supports this answer."
```

---

# 9. Human-in-the-Loop

Sensitive actions require explicit approval.

LangGraph's interruption mechanism is used to pause execution.

Conceptually:

```text
Graph
 │
 ▼
Plan Action
 │
 ▼
interrupt()
 │
 │
 ├──── Graph execution pauses
 │
 ▼
Human reviews action
 │
 ├── Approve
 │
 │     ▼
 │   Resume
 │     ▼
 │   Execute
 │
 └── Reject
       ▼
     Resume
       ▼
     Cancel
```

A persistent checkpointer allows the workflow to resume using the same conversation/thread state.

In LangGraph (v0.2+), human-in-the-loop uses native interruption:
1. The node suspends execution: `interrupt({"action": pending_action, "details": ...})`
2. The client reviews the planned action via FastAPI.
3. The `/approve` endpoint resumes execution using `Command`:
   `graph.invoke(Command(resume={"status": "approved"}), config=config)`

---

# 10. Persistence

PolicyDesk uses an async PostgreSQL-backed checkpointer (`AsyncPostgresSaver` from `langgraph-checkpoint-postgres` to avoid blocking FastAPI's async event loop).

Conceptually:

```text
                 LangGraph
                    │
                    ▼
             ┌──────────────┐
             │    State     │
             └──────┬───────┘
                    │
                    ▼
             Checkpoint
                    │
                    ▼
          PostgreSQL Database
                    │
                    ▼
              thread_id
```

This enables workflows to survive interruption and continue from stored state.

---

# 11. Tools

The project contains controlled application tools.

### Account Read Tool

Read-only operation:

```text
get_account(account_id)
```

### Dispute Tool

State-changing operation:

```text
create_dispute(...)
```

The important security distinction is:

```text
READ
 ↓
Usually safe to execute automatically

WRITE
 ↓
Validate
 ↓
Require approval
 ↓
Execute
```

---

# 12. Guardrails

The application should protect against:

* Prompt injection
* Instructions embedded in retrieved documents
* Unauthorized tool usage
* Invalid LLM output
* Out-of-scope requests
* Missing retrieval evidence
* Unapproved state-changing actions
* Infinite graph loops

The general principle is:

> **Treat LLM output as untrusted input.**

The application should validate and constrain what the model is allowed to do.

---

# 13. Evaluation

The project will include a small **golden evaluation dataset**.

Approximately:

```text
40–50 test cases
```

covering:

```text
Policy questions
Unanswerable questions
Account lookups
Dispute actions
Human escalation
Prompt-injection/adversarial cases
```

The system will measure things such as:

```text
Retrieval Hit@K
MRR
Routing accuracy
Groundedness
Answer correctness
Refusal/escalation correctness
Tool success
Approval correctness
Latency
Token usage
```

---

# 14. Baseline vs LangGraph System

A particularly important part of the project is comparing the complete workflow against a simpler baseline.

### Baseline

```text
Question
   ↓
Retrieve
   ↓
LLM
   ↓
Answer
```

### PolicyDesk

```text
Question
   ↓
Sanitize
   ↓
Classify
   ↓
Route
   ├── RAG
   │    ├── Retrieve
   │    ├── Grade
   │    ├── Rewrite
   │    └── Grounding Check
   │
   ├── Tools
   │
   ├── HITL
   │
   ├── Human Handoff
   │
   └── Refusal
```

This demonstrates **why the additional workflow complexity exists** rather than using LangGraph simply because it is a popular framework.

---

# 15. Observability

The application should record useful execution information.

For example:

```text
Request ID
Thread ID
Intent
Node name
Node latency
LLM latency
Retrieval latency
Number of retrieval attempts
Token usage
Tool execution
Errors
Final outcome
```

The deployment will use CloudWatch for application logs.

The goal is to make failures diagnosable rather than simply returning:

```text
Something went wrong.
```

---

# 16. AWS Deployment

The first deployment should remain intentionally simple.

```text
                     Internet
                         │
                         ▼
                 Security Group
                         │
                         ▼
                  ┌────────────┐
                  │    EC2     │
                  │            │
                  │  Docker    │
                  │            │
                  │ FastAPI    │
                  │ PolicyDesk │
                  └─────┬──────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
          ▼             ▼             ▼
     PostgreSQL      Bedrock      CloudWatch
      + pgvector        │             │
                        │             │
                        └─────────────┘
```

Supporting AWS services:

* EC2
* ECR
* IAM
* Amazon Bedrock
* CloudWatch
* SSM

The first version intentionally does **not** require EKS, OpenSearch, Lambda, API Gateway, or Terraform.

Those can be considered later improvements.

---

# 17. Technology Stack

| Layer              | Technology              |
| ------------------ | ----------------------- |
| Language           | Python                  |
| API                | FastAPI                 |
| Agent workflow     | LangGraph               |
| LLM                | Amazon Bedrock (Prod) / Ollama (Local) |
| Embeddings         | Bedrock embeddings / Ollama (nomic-embed) |
| Vector database    | PostgreSQL + pgvector   |
| Persistence        | PostgreSQL (`AsyncPostgresSaver`) |
| Containerization   | Docker                  |
| Cloud              | AWS                     |
| Compute            | EC2                     |
| Container registry | ECR                     |
| IAM                | IAM Role                |
| Logging            | CloudWatch              |
| Session state      | LangGraph checkpointing |

---

# 18. Repository Concept

```text
PolicyDesk/
│
├── README.md
├── project.md
├── pyproject.toml
│
├── app/
│   ├── main.py              # Application entrypoint (CLI / FastAPI)
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── router.py    # V1 root router
│   │       └── endpoints/
│   │           ├── chat.py
│   │           └── approve.py
│   ├── core/
│   │   ├── config.py        # Settings & LLM_PROVIDER switch
│   │   └── security.py      # Untrusted input sanitization
│   ├── graph/
│   │   ├── state.py         # PolicyDeskState & Intent enum
│   │   ├── nodes.py         # Graph node implementations
│   │   ├── router.py        # Conditional edge routing logic
│   │   └── graph.py         # StateGraph assembly & build_graph()
│   ├── services/
│   │   ├── llm.py           # Provider adapter (Bedrock / Ollama)
│   │   ├── retrieval.py     # pgvector search & document grading
│   │   └── tools.py         # Account lookup & dispute tools
│   └── observability/
│       └── logger.py        # CloudWatch logging & metrics
│
├── data/
│   └── policies/            # Synthetic markdown/text policy files
│
├── scripts/
│   └── ingest.py            # Chunks policies & seeds pgvector
│
├── evals/
│   ├── golden_set.json
│   └── evaluate.py
│
├── tests/
│   ├── test_nodes.py
│   ├── test_router.py
│   └── test_graph.py
│
├── deploy/
│   ├── Dockerfile
│   └── docker-compose.yml
│
└── docs/
    ├── architecture.md
    ├── decisions.md
    └── failure-drills.md
```

---

# 19. Complete End-to-End Flow

The final system should conceptually operate like this:

```text
                              USER
                                │
                                ▼
                         ┌──────────────┐
                         │   FastAPI    │
                         └──────┬───────┘
                                │
                                ▼
                         ┌──────────────┐
                         │   Sanitize   │
                         └──────┬───────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │ Intent Classifier│
                       └────────┬─────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   Conditional Router   │
                    └───────────┬────────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        │                       │                        │
        ▼                       ▼                        ▼
 POLICY QUESTION          ACCOUNT LOOKUP          DISPUTE ACTION
        │                       │                        │
        ▼                       ▼                        ▼
   Retrieve Docs           Read Tool                Validate
        │                       │                        │
        ▼                       │                        ▼
    Grade Docs                 │                  Plan Action
        │                       │                        │
    ┌───┴────┐                  │                        ▼
    │        │                  │                  ┌──────────┐
   Good     Poor                │                  │ APPROVAL │
    │        │                  │                  └────┬─────┘
    │        ▼                  │                       │
    │    Rewrite Query          │                 ┌─────┴─────┐
    │        │                  │                 │           │
    │        ▼                  │              Approve     Reject
    │     Retrieve              │                 │           │
    │        │                  │                 ▼           ▼
    │        └──────┐           │              Execute      Cancel
    │               │           │                 │
    ▼               ▼           ▼                 ▼
 Draft Answer     Escalate    Response         Outcome
    │
    ▼
Grounding Check
    │
 ┌──┴────────┐
 │           │
Yes          No
 │           │
 ▼           ▼
Answer    Escalate


              HUMAN REQUEST
                    │
                    ▼
                Handoff


              OUT OF SCOPE
                    │
                    ▼
                 Refuse
```

---

# 20. What This Project Demonstrates

By the end, PolicyDesk should demonstrate practical understanding of:

### LangGraph

* State
* Nodes
* Edges
* Conditional routing
* Loops
* Reducers
* Checkpointing
* `thread_id`
* `interrupt()`
* Resume
* Recursion limits

### RAG

* Document ingestion
* Chunking
* Embeddings
* Vector search
* Retrieval quality
* Query rewriting
* Grounding
* Evaluation

### LLM Engineering

* Structured output
* Tool calling
* Validation
* Timeouts
* Retry handling
* Provider abstraction
* Token/cost awareness

### Backend Engineering

* FastAPI
* Async I/O
* Pydantic
* Error handling
* Timeouts
* Idempotency
* Database connections
* API design

### AI Safety

* Prompt injection defense
* Least-privilege tools
* Human approval
* Fail-closed behavior
* Untrusted model output

### Cloud

* EC2
* ECR
* IAM
* Bedrock
* Security Groups
* CloudWatch
* SSM
* Docker deployment

### Evaluation

* Golden datasets
* Baselines
* Retrieval metrics
* Groundedness
* Correctness
* Latency
* Cost
* Failure testing

---

# 21. Final Project Goal

The finished project should not simply be:

> "I built a chatbot using LangGraph."

It should demonstrate:

> **I built a stateful AI workflow that can route different classes of requests, perform corrective RAG, use controlled application tools, verify grounding, pause for human approval before sensitive actions, persist execution state, evaluate quality against a baseline, and deploy the service on AWS.**

That is the core engineering story behind PolicyDesk.
