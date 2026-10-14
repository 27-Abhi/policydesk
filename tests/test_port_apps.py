import pytest
from fastapi.testclient import TestClient

from app.admin_app import admin_app
from app.customer_app import customer_app


@pytest.fixture
def customer_client():
    return TestClient(customer_app)


@pytest.fixture
def admin_client():
    return TestClient(admin_app)


class TestCustomerApp:
    def test_customer_portal_html(self, customer_client):
        resp = customer_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "PolicyDesk" in resp.text
        assert "Customer Portal" in resp.text

    def test_customer_health(self, customer_client):
        resp = customer_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["port"] == 8000

    def test_customer_chat_completion(self, customer_client):
        payload = {
            "messages": [{"role": "user", "content": "What is the fee for an international wire?"}]
        }
        resp = customer_client.post("/v1/chat/completions", json=payload)
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        assert "wire" in content.lower() or "$45" in content


class TestAdminApp:
    def test_admin_portal_html(self, admin_client):
        resp = admin_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Compliance & Staff Portal" in resp.text

    def test_admin_health(self, admin_client):
        resp = admin_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["port"] == 8001

    def test_admin_escalation_endpoint(self, admin_client):
        resp = admin_client.get("/v1/escalations")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestCrossPortalIntegration:
    def test_dispute_submitted_on_customer_reviewed_on_admin(self, customer_client, admin_client):
        thread_id = "cross-portal-verification-thread"

        # 1. Customer initiates dispute on Port 8000
        customer_resp = customer_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Dispute transaction TX-3001"}]},
            headers={"X-Thread-ID": thread_id},
        )
        assert customer_resp.status_code == 200
        answer = customer_resp.json()["choices"][0]["message"]["content"]
        assert "submitted for compliance review" in answer

        # 2. Compliance officer views dispute on Port 8001
        admin_disputes_resp = admin_client.get("/v1/disputes")
        assert admin_disputes_resp.status_code == 200
        disputes = admin_disputes_resp.json()
        target = next((d for d in disputes if d["thread_id"] == thread_id), None)
        assert target is not None
        assert target["is_pending_review"] is True
        assert target["transaction_id"] == "TX-3001"

        # 3. Compliance officer approves case on Port 8001
        review_resp = admin_client.post(
            f"/v1/disputes/{thread_id}/review",
            json={"approved": True, "comments": "Merchant billing discrepancy confirmed"},
        )
        assert review_resp.status_code == 200
        review_data = review_resp.json()
        assert review_data["dispute_status"] == "approved"
        assert review_data["dispute_notification_sent"] is True

        # 4. Verifying status on Port 8001 shows completed review
        status_resp = admin_client.get(f"/v1/disputes/{thread_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["is_pending_review"] is False
        assert status_resp.json()["dispute_status"] == "approved"
