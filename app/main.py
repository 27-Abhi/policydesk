"""PolicyDesk application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.graph.graph import build_graph

app = FastAPI(
    title="PolicyDesk API",
    description="Escalation-aware fintech policy support agent with LangGraph RAG",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix="/v1")
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "app": "PolicyDesk"}


def run_demo():
    """Run sample inputs through the PolicyDesk graph."""
    graph = build_graph()

    samples = [
        "What is the transaction dispute policy?",
        "What is my account balance?",
        "I want to dispute transaction TX123",
        "I want to speak to a human",
        "Tell me a joke",
        "   ",
    ]

    print("=" * 60)
    print("PolicyDesk - Intent Routing Demonstration")
    print("=" * 60)

    for prompt in samples:
        initial_state = {"user_input": prompt}
        result = graph.invoke(initial_state)
        print(f"Input:   {prompt!r}")
        print(f"Intent:  {result.get('intent')}")
        print(f"Route:   {result.get('route')}")
        if result.get("errors"):
            print(f"Errors:  {result.get('errors')}")
        print("-" * 60)


if __name__ == "__main__":
    run_demo()
