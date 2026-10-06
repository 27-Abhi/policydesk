"""PolicyDesk application entry point."""

from app.graph.graph import build_graph


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
