import pytest


@pytest.fixture(autouse=True)
def disable_external_services_during_tests(monkeypatch):
    """Ensure automated tests never consume LangSmith quota or require live LLMs."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CHECKPOINT_DB_PATH", ":memory:")
