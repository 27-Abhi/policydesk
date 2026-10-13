import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestChatCompletionsAPI:
    def test_sync_policy_completion(self, client):
        payload = {
            "model": "policydesk",
            "messages": [
                {"role": "user", "content": "What is the international wire transfer fee policy?"}
            ],
            "stream": False,
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["object"] == "chat.completion"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        content = data["choices"][0]["message"]["content"]
        assert "$45" in content or "wire transfer" in content.lower()

    def test_api_v1_prefix_compatibility(self, client):
        payload = {
            "messages": [
                {"role": "user", "content": "What is the account maintenance fee policy?"}
            ]
        }
        response = client.post("/api/v1/chat/completions", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["content"]

    def test_streaming_chat_completion(self, client):
        payload = {
            "model": "policydesk",
            "messages": [
                {"role": "user", "content": "What is the policy for transaction disputes?"}
            ],
            "stream": True,
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        body = response.text
        assert "data: " in body
        assert "[DONE]" in body

    def test_empty_messages_validation_error(self, client):
        payload = {"messages": []}
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 422

    def test_missing_user_message_validation_error(self, client):
        payload = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."}
            ]
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 422
