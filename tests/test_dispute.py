import pytest
from fastapi.testclient import TestClient
from langgraph.types import Command

from app.graph.graph import build_graph
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestDisputeGraphHITL:
    def test_dispute_graph_interrupt_pause(self):
        graph = build_graph()
        thread_id = "test-thread-dispute-pause"

        result = graph.invoke(
            {"user_input": "I need to dispute unauthorized transaction TXN-9988"},
            config={"configurable": {"thread_id": thread_id}},
        )

        assert "__interrupt__" in result
        assert result["dispute_status"] == "pending_human_review"
        assert result["dispute_id"].startswith("DISP-")
        assert result["transaction_id"] == "TXN-9988"

        state = graph.get_state({"configurable": {"thread_id": thread_id}})
        assert state.next == ("require_human_approval",)

    def test_dispute_graph_resume_approved(self):
        graph = build_graph()
        thread_id = "test-thread-dispute-approved"

        # Initial trigger pauses at human approval
        graph.invoke(
            {"user_input": "Please dispute chargeback for transaction TX-101"},
            config={"configurable": {"thread_id": thread_id}},
        )

        # Resume with approval
        resumed = graph.invoke(
            Command(resume={"approved": True, "comments": "Confirmed bank clerical error"}),
            config={"configurable": {"thread_id": thread_id}},
        )

        assert resumed["dispute_status"] == "approved"
        assert resumed["dispute_notification_sent"] is True
        assert "approved by compliance" in resumed["answer"]
        assert "disputes-ops@bank.internal" in resumed["answer"] or "operations team" in resumed["answer"]

        # Thread is now complete
        state = graph.get_state({"configurable": {"thread_id": thread_id}})
        assert state.next == ()

    def test_dispute_graph_resume_rejected(self):
        graph = build_graph()
        thread_id = "test-thread-dispute-rejected"

        # Initial trigger pauses at human approval
        graph.invoke(
            {"user_input": "Dispute transaction TRX-777"},
            config={"configurable": {"thread_id": thread_id}},
        )

        # Resume with rejection
        resumed = graph.invoke(
            Command(resume={"approved": False, "comments": "Exceeded 60-day reporting window"}),
            config={"configurable": {"thread_id": thread_id}},
        )

        assert resumed["dispute_status"] == "rejected"
        assert resumed["dispute_notification_sent"] is False
        assert "not approved" in resumed["answer"]
        assert "Exceeded 60-day reporting window" in resumed["answer"]

        # Thread is now complete
        state = graph.get_state({"configurable": {"thread_id": thread_id}})
        assert state.next == ()


class TestDisputeAPIEndpoints:
    def test_api_dispute_review_approved_flow(self, client):
        # 1. User submits dispute through chat completions
        payload = {
            "messages": [
                {"role": "user", "content": "Unauthorized charge on my account: dispute transaction TX-4455"}
            ]
        }
        create_resp = client.post("/v1/chat/completions", json=payload)
        assert create_resp.status_code == 200

        thread_id = create_resp.headers.get("X-Thread-ID")
        assert thread_id is not None
        initial_answer = create_resp.json()["choices"][0]["message"]["content"]
        assert "submitted for compliance review" in initial_answer

        # 2. Reviewer inspects dispute status
        status_resp = client.get(f"/v1/disputes/{thread_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["transaction_id"] == "TX-4455"
        assert status_data["is_pending_review"] is True
        assert status_data["dispute_status"] == "pending_human_review"

        # 3. Compliance approves dispute
        review_resp = client.post(
            f"/v1/disputes/{thread_id}/review",
            json={"approved": True, "comments": "Verified fraudulent merchant signature"},
        )
        assert review_resp.status_code == 200
        review_data = review_resp.json()
        assert review_data["dispute_status"] == "approved"
        assert review_data["dispute_notification_sent"] is True
        assert review_data["transaction_id"] == "TX-4455"
        assert "approved by compliance" in review_data["answer"]

        # 4. Attempting to review an already processed dispute returns 400
        repeat_resp = client.post(
            f"/v1/disputes/{thread_id}/review",
            json={"approved": False, "comments": "Duplicate call"},
        )
        assert repeat_resp.status_code == 400

    def test_api_dispute_review_rejected_flow(self, client):
        thread_id = "custom-dispute-test-reject"

        # Submit dispute with explicit X-Thread-ID
        client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "I want to dispute transaction TX-9999"}]},
            headers={"X-Thread-ID": thread_id},
        )

        # Reject dispute
        review_resp = client.post(
            f"/v1/disputes/{thread_id}/review",
            json={"approved": False, "comments": "No merchant fraud detected"},
        )
        assert review_resp.status_code == 200
        review_data = review_resp.json()
        assert review_data["dispute_status"] == "rejected"
        assert review_data["dispute_notification_sent"] is False
        assert "not approved" in review_data["answer"]
        assert "No merchant fraud detected" in review_data["answer"]

    def test_api_dispute_not_found(self, client):
        resp = client.get("/v1/disputes/nonexistent-thread-xyz")
        assert resp.status_code == 404

        review_resp = client.post(
            "/v1/disputes/nonexistent-thread-xyz/review",
            json={"approved": True, "comments": "N/A"},
        )
        assert review_resp.status_code == 404
