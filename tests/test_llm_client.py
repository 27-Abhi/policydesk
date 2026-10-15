import pytest
from app.llm.client import (
    AnthropicClient,
    DeterministicMockClient,
    OllamaClient,
    OpenAIClient,
    get_llm_client,
)
from app.graph.rag_nodes import generate_answer


class TestDeterministicMockClient:
    def test_generate_grounded_mock(self):
        client = DeterministicMockClient()
        output = client.generate(
            query="What is the wire transfer fee?",
            context="Domestic wire transfers incur a $25 fee.",
        )
        assert output.startswith("According to bank policy:")
        assert "$25 fee" in output

    def test_stream_grounded_mock(self):
        client = DeterministicMockClient()
        tokens = list(
            client.stream(
                query="What is the fee?",
                context="Fee is $25.",
            )
        )
        reconstructed = "".join(tokens).strip()
        assert "According to bank policy:" in reconstructed
        assert "$25" in reconstructed


class TestProviderFactoryResolution:
    def test_default_mock_when_no_env_keys(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        client = get_llm_client()
        assert isinstance(client, DeterministicMockClient)

    def test_anthropic_priority(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-key")

        client = get_llm_client()
        assert isinstance(client, AnthropicClient)
        assert client.api_key == "sk-ant-test-key"

    def test_openai_selection(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-key")

        client = get_llm_client()
        assert isinstance(client, OpenAIClient)
        assert client.api_key == "sk-proj-test-key"

    def test_ollama_selection(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

        client = get_llm_client()
        assert isinstance(client, OllamaClient)
        assert client.base_url == "http://localhost:11434"


class TestGracefulDegradation:
    def test_openai_api_error_fallback(self):
        client = OpenAIClient(api_key="invalid-key-xyz")
        # Invalid endpoint or network error should gracefully return mock answer rather than crash
        output = client.generate("query", "Policy clause fallback context.")
        assert "According to bank policy:" in output
        assert "Policy clause fallback context." in output

    def test_anthropic_api_error_fallback(self):
        client = AnthropicClient(api_key="invalid-key-xyz")
        output = client.generate("query", "Anthropic clause fallback context.")
        assert "According to bank policy:" in output
        assert "Anthropic clause fallback context." in output

    def test_ollama_api_error_fallback(self):
        client = OllamaClient(base_url="http://localhost:99999")
        output = client.generate("query", "Ollama clause fallback context.")
        assert "According to bank policy:" in output
        assert "Ollama clause fallback context." in output


class TestRAGNodeIntegration:
    def test_generate_answer_with_mock_client(self):
        state = {
            "user_input": "What is the domestic wire fee?",
            "is_relevant": True,
            "reranked_docs": ["Domestic wire transfers incur a $25 fee."],
        }
        res = generate_answer(state)
        assert res["route"] == "policy_question"
        assert "$25 fee" in res["answer"]
