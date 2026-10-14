import pyotp
import pytest

from app.graph.graph import build_graph
from app.tools.bank_db import SyntheticBankDatabase, get_bank_db


class TestBankDatabaseAnd2FA:
    def test_bank_db_account_and_tx_lookup(self):
        db = SyntheticBankDatabase()
        acc = db.get_account_by_phone("5550192834")
        assert acc is not None
        assert acc.account_number == "ACC-1049"
        assert acc.balance == 4250.00

        txs = db.get_transactions("ACC-1049", limit=5)
        assert len(txs) >= 4
        assert any("Payroll" in t.description for t in txs)

    def test_bank_db_2fa_dynamic_and_master(self):
        db = SyntheticBankDatabase()
        phone = "5550192834"

        # Dispatch code
        ok, msg = db.send_otp(phone)
        assert ok is True
        assert "dispatched" in msg

        # Read generated code
        clean_phone = "5550192834"
        generated_code = db._pending_otps[clean_phone]["code"]

        # Dynamic code verifies
        verified, _ = db.verify_otp(phone, generated_code)
        assert verified is True

        # Master code also verifies
        db.send_otp(phone)
        master_verified, _ = db.verify_otp(phone, "123456")
        assert master_verified is True

    def test_bank_db_2fa_pyotp_totp(self):
        db = SyntheticBankDatabase()
        acc = db.get_account_by_phone("5550192834")
        totp = pyotp.TOTP(acc.totp_secret)
        current_token = totp.now()

        # No active SMS dispatch needed; standard Authenticator token verifies
        verified, msg = db.verify_otp("5550192834", current_token)
        assert verified is True
        assert "authenticator" in msg.lower()

    def test_bank_db_2fa_lockout(self):
        db = SyntheticBankDatabase()
        phone = "5550192834"
        db.send_otp(phone)

        # 1st fail
        v1, _ = db.verify_otp(phone, "000000")
        assert v1 is False

        # 2nd fail
        v2, _ = db.verify_otp(phone, "000001")
        assert v2 is False

        # 3rd fail locks
        v3, msg3 = db.verify_otp(phone, "000002")
        assert v3 is False
        assert "locked" in msg3.lower()

        # Subsequent attempts are blocked
        v4, msg4 = db.verify_otp(phone, "123456")
        # Pending session is locked
        clean_phone = "5550192834"
        assert db._pending_otps[clean_phone]["locked"] is True


class TestAccountLookupMultiTurn:
    def test_multi_turn_2fa_and_balance_flow(self):
        graph = build_graph()
        thread_id = "test-session-acc-lookup-1"
        config = {"configurable": {"thread_id": thread_id}}

        # Turn 1: User asks for balance without phone
        res1 = graph.invoke({"user_input": "What is my account balance?"}, config=config)
        assert res1["route"] == "account_lookup"
        assert res1["authenticated"] is False
        assert "provide your registered 10-digit mobile phone" in res1["answer"]

        # Turn 2: User provides phone number
        res2 = graph.invoke({"user_input": "My phone is 555-019-2834"}, config=config)
        assert res2["route"] == "account_lookup"
        assert res2["authenticated"] is False
        assert res2["auth_step"] == "awaiting_otp"
        assert "verification code has been dispatched" in res2["answer"]

        # Turn 3: User enters master OTP 123456
        res3 = graph.invoke({"user_input": "123456"}, config=config)
        assert res3["route"] == "account_lookup"
        assert res3["authenticated"] is True
        assert res3["account_number"] == "ACC-1049"
        assert "$4,250.00" in res3["answer"]

        # Turn 4: Authenticated user asks for recent transaction history
        res4 = graph.invoke({"user_input": "Show my recent transactions"}, config=config)
        assert res4["route"] == "account_lookup"
        assert "Whole Foods Market" in res4["answer"] or "ACME Payroll" in res4["answer"]
        assert "$4,250.00" in res4["answer"]


class TestCustomerCareEscalation:
    def test_critical_escalation(self):
        graph = build_graph()
        res = graph.invoke({"user_input": "I need to speak to an agent immediately! My debit card was stolen and I see unauthorized charges!"})

        assert res["route"] == "human_request"
        assert res["escalation_priority"] == "P1_CRITICAL"
        assert res["escalation_ticket_id"].startswith("TICK-")
        assert "Customer Care" in res["answer"]
        assert "< 3 minutes" in res["answer"]

    def test_standard_escalation(self):
        graph = build_graph()
        res = graph.invoke({"user_input": "Can I please speak with a human customer support person?"})

        assert res["route"] == "human_request"
        assert res["escalation_priority"] == "P3_STANDARD"
        assert res["escalation_ticket_id"].startswith("TICK-")
        assert "Customer Care" in res["answer"]
        assert "< 20 minutes" in res["answer"]
