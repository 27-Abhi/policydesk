import pytest


@pytest.fixture(autouse=True)
def disable_langsmith_during_tests(monkeypatch):
    """Ensure automated tests never consume LangSmith quota."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
