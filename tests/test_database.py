from pathlib import Path
import pytest
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.db.session import is_postgres_configured, get_database_url
from app.graph.graph import build_graph
from app.tools.bank_db import (
    PostgresBankDatabase,
    SyntheticBankDatabase,
    get_bank_db,
    normalize_phone,
    mask_phone,
)


class TestDatabaseSchemaAndSession:
    def test_schema_sql_exists_and_valid(self):
        schema_path = Path(__file__).resolve().parent.parent / "app" / "db" / "schema.sql"
        assert schema_path.exists(), "schema.sql must exist in app/db/"
        content = schema_path.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS accounts" in content
        assert "CREATE TABLE IF NOT EXISTS transactions" in content
        assert "CREATE TABLE IF NOT EXISTS disputes" in content
        assert "CREATE TABLE IF NOT EXISTS escalations" in content
        assert "ON CONFLICT (account_number) DO NOTHING" in content

    def test_is_postgres_configured_false_during_tests(self):
        assert not is_postgres_configured()
        assert get_database_url() is None

    def test_get_bank_db_returns_synthetic_fallback(self):
        db = get_bank_db()
        assert isinstance(db, SyntheticBankDatabase)


class TestSqliteSaverCheckpointEngine:
    def test_sqlite_saver_hitl_dispute_pause_and_resume(self):
        # Create an in-memory SQLite connection with WAL mode
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass

        checkpointer = SqliteSaver(conn)
        graph = build_graph(checkpointer=checkpointer)

        thread_id = "test_thread_sqlite_001"
        config = {"configurable": {"thread_id": thread_id}}

        # 1. Trigger dispute action -> pauses at interrupt()
        res1 = graph.invoke(
            {"user_input": "I want to dispute transaction TXN-9012 for $850"},
            config=config,
        )
        assert res1.get("dispute_status") == "pending_human_review"
        assert res1.get("transaction_id") == "TXN-9012"
        assert res1.get("__interrupt__") is not None

        # 2. Verify state is persisted in SQLite checkpointer
        state_tuple = graph.get_state(config)
        assert state_tuple.values.get("dispute_id") is not None

        # 3. Resume with approval using Command(resume=...)
        res2 = graph.invoke(
            Command(resume={"approved": True, "comments": "Approved by supervisor"}),
            config=config,
        )
        assert res2.get("dispute_status") == "approved"
        assert "approved by compliance" in res2.get("answer", "").lower()

    def test_sqlite_saver_rejection_cycle(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        graph = build_graph(checkpointer=checkpointer)

        thread_id = "test_thread_sqlite_002"
        config = {"configurable": {"thread_id": thread_id}}

        graph.invoke(
            {"user_input": "Dispute transaction TXN-9011"},
            config=config,
        )

        res = graph.invoke(
            Command(resume={"approved": False, "comments": "Documentation invalid"}),
            config=config,
        )
        assert res.get("dispute_status") == "rejected"
        assert "not approved" in res.get("answer", "").lower()


class TestBankDatabaseFallbacks:
    def test_postgres_bank_db_graceful_memory_fallback(self):
        # Instantiating PostgresBankDatabase with no active connection pool
        # should safely degrade to in-memory store without throwing unhandled errors
        pg_db = PostgresBankDatabase()
        acc = pg_db.get_account_by_phone("555-019-2834")
        assert acc is not None
        assert acc.account_number == "ACC-1049"

        txs = pg_db.get_transactions("ACC-1049", limit=2)
        assert len(txs) == 2

    def test_phone_normalization_and_masking(self):
        assert normalize_phone("1-555-019-2834") == "5550192834"
        assert normalize_phone("(555) 019-2834") == "5550192834"
        masked = mask_phone("5550192834")
        assert "2834" in masked
        assert "***" in masked
