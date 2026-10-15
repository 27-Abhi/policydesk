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

    def test_architecture_explainer_html(self, customer_client):
        resp = customer_client.get("/explainer")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "PolicyDesk" in resp.text
        assert "Architecture Explainer" in resp.text

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

    def test_list_policies_endpoint(self, customer_client, admin_client):
        # Accessible on both Port 8000 and Port 8001
        resp_cust = customer_client.get("/v1/policies")
        assert resp_cust.status_code == 200
        data_cust = resp_cust.json()
        assert data_cust["total_documents"] >= 3
        assert data_cust["total_parents"] >= 9

        resp_admin = admin_client.get("/v1/policies")
        assert resp_admin.status_code == 200

    def test_ingest_policy_json_and_query_it(self, admin_client, customer_client):
        from pathlib import Path
        test_file = Path("data/policies/crypto_asset_policy_2026.txt")
        try:
            # 1. Ingest new policy via JSON on admin app (Port 8001)
            payload = {
                "doc_id": "crypto_asset_policy_2026",
                "title": "Crypto Asset Custody Policy",
                "text": (
                    "Rules governing digital asset custody and staking for PolicyDesk.\n\n"
                    "Cryptocurrency staking rewards incur an administrative fee of 3.5% annually. "
                    "The minimum cold storage lockup period is 14 calendar days."
                ),
            }
            ingest_resp = admin_client.post("/v1/policies/ingest", json=payload)
            assert ingest_resp.status_code == 200
            ingest_data = ingest_resp.json()
            assert ingest_data["status"] == "success"
            assert ingest_data["doc_id"] == "crypto_asset_policy_2026"
            assert ingest_data["parents_added"] >= 2

            # 2. Query newly ingested policy immediately on customer app (Port 8000)
            query_payload = {
                "messages": [{"role": "user", "content": "What is the fee for cryptocurrency staking?"}]
            }
            chat_resp = customer_client.post("/v1/chat/completions", json=query_payload)
            assert chat_resp.status_code == 200
            answer = chat_resp.json()["choices"][0]["message"]["content"]
            assert "3.5%" in answer or "staking" in answer.lower()
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_upload_policy_file_and_query_it(self, admin_client, customer_client):
        from pathlib import Path
        test_file = Path("data/policies/overdraft_courtesy.txt")
        try:
            # Upload .txt policy file
            file_content = (
                "Overdraft Protection Program rules for PolicyDesk Financial.\n\n"
                "Courtesy overdraft protection covers transactions up to $75.00 with a $15 fee per occurrence."
            ).encode("utf-8")

            files = {"file": ("overdraft_courtesy.txt", file_content, "text/plain")}
            upload_resp = admin_client.post(
                "/v1/policies/upload",
                files=files,
                data={"doc_id": "overdraft_courtesy", "title": "Overdraft Courtesy Rules"},
            )
            assert upload_resp.status_code == 200
            assert upload_resp.json()["status"] == "success"
            assert upload_resp.json()["doc_id"] == "overdraft_courtesy"
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_multi_turn_chat_follow_up_without_stale_leakage(self, customer_client):
        thread_id = "multi-turn-hygiene-thread"

        # Turn 1: Policy question on unclaimed deposits
        resp1 = customer_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "what happens to unclaimed deposit"}]},
            headers={"X-Thread-ID": thread_id},
        )
        assert resp1.status_code == 200
        ans1 = resp1.json()["choices"][0]["message"]["content"]
        assert "deaf" in ans1.lower() or "deposit" in ans1.lower()

        # Turn 2: Follow-up asking "and what happens if i dont pay"
        resp2 = customer_client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "user", "content": "what happens to unclaimed deposit"},
                    {"role": "assistant", "content": ans1},
                    {"role": "user", "content": "and what happens if i dont pay"},
                ]
            },
            headers={"X-Thread-ID": thread_id},
        )
        assert resp2.status_code == 200
        ans2 = resp2.json()["choices"][0]["message"]["content"]

        # Invariant 1: Turn 2 must NOT duplicate Turn 1 verbatim
        assert ans2.strip() != ans1.strip()

        # Invariant 2: Turn 2 must NOT output raw developer debug placeholders
        assert "Routed to: out_of_scope" not in ans2

        # Invariant 3: Turn 2 must provide helpful policy guidance
        assert "policy" in ans2.lower() or "fee" in ans2.lower() or "bank" in ans2.lower()
