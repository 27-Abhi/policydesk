-- =====================================================================
-- PolicyDesk Relational Database Schema
-- Optimized for PostgreSQL 16 (AWS EC2 t2.micro / t3.micro Free Tier)
-- =====================================================================

-- 1. Accounts Table (Core Banking & Row-Locked 2FA State)
CREATE TABLE IF NOT EXISTS accounts (
    account_number VARCHAR(16) PRIMARY KEY,
    customer_name VARCHAR(64) NOT NULL,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    account_type VARCHAR(32) NOT NULL DEFAULT 'Checking',
    balance NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    holds NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    overdraft_limit NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    totp_secret VARCHAR(64),
    otp_code VARCHAR(12),
    otp_expires_at TIMESTAMPTZ,
    failed_auth_attempts INT NOT NULL DEFAULT 0,
    is_locked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS otp_code VARCHAR(12);
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS otp_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_accounts_phone ON accounts(phone_number);

-- 2. Transactions Table (Double-Entry Ledger)
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id VARCHAR(32) PRIMARY KEY,
    account_number VARCHAR(16) NOT NULL REFERENCES accounts(account_number) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    tx_type VARCHAR(16) NOT NULL,
    is_flagged BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_number, timestamp DESC);

-- 3. Disputes Table (Compliance Human-in-the-Loop Workflow)
CREATE TABLE IF NOT EXISTS disputes (
    dispute_id VARCHAR(32) PRIMARY KEY,
    thread_id VARCHAR(64) NOT NULL,
    transaction_id VARCHAR(32) NOT NULL,
    account_number VARCHAR(16),
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_HUMAN_REVIEW',
    comments TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_disputes_thread ON disputes(thread_id);
CREATE INDEX IF NOT EXISTS idx_disputes_status ON disputes(status);

-- 4. Customer Care Escalations Table
CREATE TABLE IF NOT EXISTS escalations (
    ticket_id VARCHAR(32) PRIMARY KEY,
    thread_id VARCHAR(64) NOT NULL,
    priority VARCHAR(16) NOT NULL DEFAULT 'P3_STANDARD',
    reason TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_escalations_status ON escalations(status);

-- =====================================================================
-- Initial Seed Data (Idempotent: ON CONFLICT DO NOTHING)
-- =====================================================================

INSERT INTO accounts (account_number, customer_name, phone_number, account_type, balance, holds, overdraft_limit, totp_secret, failed_auth_attempts, is_locked)
VALUES
    ('ACC-1049', 'Alex Morgan', '+15550192834', 'Primary Checking', 4250.00, 0.00, 500.00, 'JBSWY3DPEHPK3PXP', 0, FALSE),
    ('ACC-2088', 'Jordan Lee', '+15550149921', 'High-Yield Savings', 18420.50, 250.00, 0.00, 'JBSWY3DPEHPK3PXQ', 0, FALSE)
ON CONFLICT (account_number) DO NOTHING;

INSERT INTO transactions (transaction_id, account_number, timestamp, description, amount, tx_type, is_flagged)
VALUES
    ('TXN-9011', 'ACC-1049', NOW() - INTERVAL '1 day', 'Direct Deposit - ACME Payroll', 2450.00, 'CREDIT', FALSE),
    ('TXN-9012', 'ACC-1049', NOW() - INTERVAL '2 days', 'Whole Foods Market', 84.50, 'DEBIT', TRUE),
    ('TXN-9013', 'ACC-1049', NOW() - INTERVAL '3 days', 'City Power & Electric', 120.00, 'DEBIT', FALSE),
    ('TXN-9014', 'ACC-1049', NOW() - INTERVAL '4 days', 'Starbucks Coffee', 15.75, 'DEBIT', FALSE),
    ('TXN-8801', 'ACC-2088', NOW() - INTERVAL '5 days', 'Transfer from Primary Checking', 5000.00, 'CREDIT', FALSE),
    ('TXN-8802', 'ACC-2088', NOW() - INTERVAL '10 days', 'Monthly Interest Payment (4.25% APY)', 62.15, 'CREDIT', FALSE)
ON CONFLICT (transaction_id) DO NOTHING;
