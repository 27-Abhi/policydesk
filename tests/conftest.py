import os
import pytest


# Isolate environment before module-level fixtures or app imports run
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["CHECKPOINT_DB_PATH"] = ":memory:"
os.environ["VECTOR_DB_PATH"] = ":memory:"
os.environ["ENABLE_REMOTE_EMBEDDINGS"] = "false"
os.environ["USE_LLM_ROUTER"] = "false"
os.environ["ENABLE_LLM_RERANK"] = "false"
os.environ.pop("ADMIN_API_KEY", None)


@pytest.fixture(autouse=True)
def disable_external_services_during_tests(monkeypatch):
    """Ensure automated tests never consume LangSmith quota or require live LLMs."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_AI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.setenv("CHECKPOINT_DB_PATH", ":memory:")
    monkeypatch.setenv("VECTOR_DB_PATH", ":memory:")
    monkeypatch.setenv("ENABLE_REMOTE_EMBEDDINGS", "false")
    monkeypatch.setenv("USE_LLM_ROUTER", "false")
    monkeypatch.setenv("ENABLE_LLM_RERANK", "false")
