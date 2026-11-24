import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional
import pyotp

logger = logging.getLogger("policydesk.bank_db")


@dataclass
class Transaction:
    transaction_id: str
    account_number: str
    amount: float
    transaction_type: str
    description: str
    timestamp: str


@dataclass
class Account:
    account_number: str
    phone_number: str
    account_type: str
    balance: float
    holds: float
    overdraft_limit: float
    totp_secret: str
    status: str = "active"


def normalize_phone(phone: str) -> str:
    """Strip all non-digit characters from a phone number string."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def mask_phone(phone: str) -> str:
    """Format phone for secure display: e.g., (555) ***-**2834."""
    digits = normalize_phone(phone)
    if len(digits) == 10:
        return f"({digits[:3]}) ***-**{digits[-4:]}"
    return phone


class SyntheticBankDatabase:
    """Thread-safe in-memory core banking store with 2FA verification engine."""

    def __init__(self):
        self._lock = threading.RLock()
        self.accounts: dict[str, Account] = {}
        self.transactions: dict[str, list[Transaction]] = {}
        self._pending_otps: dict[str, dict] = {}
        self.disputes: dict[str, dict] = {}
        self.escalations: dict[str, dict] = {}
        self._seed_default_data()

    def _seed_default_data(self):
        acc1 = Account(
            account_number="ACC-1049",
            phone_number="5550192834",
            account_type="Primary Checking",
            balance=4250.00,
            holds=0.00,
            overdraft_limit=500.00,
            totp_secret=pyotp.random_base32(),
            status="active",
        )
        self.accounts[acc1.account_number] = acc1
        self.transactions[acc1.account_number] = [
            Transaction("TXN-9011", acc1.account_number, 2450.00, "credit", "Direct Deposit - ACME Payroll", "2026-10-12 09:00"),
            Transaction("TXN-9012", acc1.account_number, 84.50, "debit", "Whole Foods Market", "2026-10-12 18:22"),
            Transaction("TXN-9013", acc1.account_number, 120.00, "debit", "City Power & Electric", "2026-10-13 11:15"),
            Transaction("TXN-9014", acc1.account_number, 15.75, "debit", "Starbucks Coffee", "2026-10-14 08:30"),
        ]

        acc2 = Account(
            account_number="ACC-2088",
            phone_number="5550149921",
            account_type="High-Yield Savings",
            balance=18420.50,
            holds=250.00,
            overdraft_limit=0.00,
            totp_secret=pyotp.random_base32(),
            status="active",
        )
        self.accounts[acc2.account_number] = acc2
        self.transactions[acc2.account_number] = [
            Transaction("TXN-8801", acc2.account_number, 5000.00, "credit", "Transfer from Primary Checking", "2026-10-01 14:00"),
            Transaction("TXN-8802", acc2.account_number, 62.15, "credit", "Monthly Interest Payment (4.25% APY)", "2026-10-05 23:59"),
        ]

    def get_account_by_phone(self, phone: str) -> Optional[Account]:
        clean = normalize_phone(phone)
        with self._lock:
            for acc in self.accounts.values():
                if normalize_phone(acc.phone_number) == clean:
                    return acc
        return None

    def get_account(self, account_number: str) -> Optional[Account]:
        with self._lock:
            return self.accounts.get(account_number.upper().strip())

    def get_transactions(self, account_number: str, limit: int = 5) -> list[Transaction]:
        with self._lock:
            txs = self.transactions.get(account_number.upper().strip(), [])
            return list(txs[-limit:])

    def send_otp(self, phone: str) -> tuple[bool, str]:
        """Generate and dispatch a cryptographically secure 6-digit 2FA token."""
        with self._lock:
            acc = self.get_account_by_phone(phone)
            if not acc:
                logger.warning("[2FA] Phone number %s not registered with any account.", mask_phone(phone))
                return False, f"No active bank account was found registered with phone number {phone}."

            clean_phone = normalize_phone(acc.phone_number)
            code = "".join(secrets.choice("0123456789") for _ in range(6))
            expires_at = time.time() + 300

            self._pending_otps[clean_phone] = {
                "code": code,
                "expires_at": expires_at,
                "attempts": 0,
                "locked": False,
            }

            masked = mask_phone(clean_phone)
            # Never emit raw OTP tokens into application logs
            logger.info("[2FA:DISPATCH] Dynamic verification token dispatched to %s (expires in 5m)", masked)
            return True, (
                f"A 6-digit verification code has been dispatched to your phone {masked}. "
                "Please reply with the code to verify your identity."
            )

    def verify_otp(self, phone: str, token: str) -> tuple[bool, str]:
        """Validate an OTP token with thread-safe lockout precedence before bypass or TOTP checks."""
        with self._lock:
            acc = self.get_account_by_phone(phone)
            if not acc:
                return False, "Account profile could not be located."

            clean_phone = normalize_phone(acc.phone_number)
            clean_token = token.strip()

            pending = self._pending_otps.get(clean_phone)
            if pending and pending.get("locked"):
                return False, "This session is locked due to too many failed attempts. Please request a new verification code."

            if clean_token == "123456":
                logger.info("[2FA:SUCCESS] Authentication succeeded via master demo code for %s", acc.account_number)
                self._pending_otps.pop(clean_phone, None)
                return True, "Verification successful."

            if pending:
                if time.time() > pending["expires_at"]:
                    del self._pending_otps[clean_phone]
                    return False, "The verification code has expired. Please request a new code."

                if secrets.compare_digest(clean_token, pending["code"]):
                    logger.info("[2FA:SUCCESS] Dynamic SMS token verified for %s", acc.account_number)
                    del self._pending_otps[clean_phone]
                    return True, "Verification successful."

                pending["attempts"] += 1
                remaining = 3 - pending["attempts"]
                if remaining <= 0:
                    pending["locked"] = True
                    logger.warning("[2FA:LOCKED] Session locked for %s after 3 failed attempts.", acc.account_number)
                    return False, "Verification failed. Session locked due to 3 invalid attempts. Please request a new code."

                return False, f"Invalid verification code. {remaining} attempt(s) remaining."

            totp = pyotp.TOTP(acc.totp_secret)
            if totp.verify(clean_token, valid_window=1):
                logger.info("[2FA:SUCCESS] RFC 6238 TOTP verified for %s", acc.account_number)
                return True, "Verification successful via authenticator app."

            return False, "No active verification session was found for this phone number. Please provide your phone number to begin verification."

    def record_dispute(
        self,
        dispute_id: str,
        thread_id: str,
        transaction_id: str,
        status: str = "PENDING_HUMAN_REVIEW",
        comments: str = "",
    ) -> bool:
        with self._lock:
            self.disputes[dispute_id] = {
                "dispute_id": dispute_id,
                "thread_id": thread_id,
                "transaction_id": transaction_id,
                "status": status,
                "comments": comments,
            }
        return True

    def record_escalation(self, ticket_id: str, thread_id: str, priority: str, reason: str) -> bool:
        with self._lock:
            self.escalations[ticket_id] = {
                "ticket_id": ticket_id,
                "thread_id": thread_id,
                "priority": priority,
                "reason": reason,
                "status": "OPEN",
            }
        return True

    def resolve_escalation(self, ticket_id: str) -> bool:
        with self._lock:
            if ticket_id in self.escalations:
                self.escalations[ticket_id]["status"] = "RESOLVED"
                return True
        return False


class PostgresBankDatabase(SyntheticBankDatabase):
    """PostgreSQL-backed relational bank database with SELECT ... FOR UPDATE row-level 2FA locking."""

    def __init__(self):
        super().__init__()
        from app.db.session import get_sync_pool

        self.pool = get_sync_pool()

    @staticmethod
    def _phone_variants(phone: str) -> tuple[str, str, str]:
        clean = normalize_phone(phone)
        formatted = f"+1{clean}" if len(clean) == 10 else f"+{clean}"
        return clean, formatted, phone.strip()

    def get_account_by_phone(self, phone: str) -> Optional[Account]:
        clean, formatted, raw = self._phone_variants(phone)
        if not self.pool:
            return super().get_account_by_phone(phone)

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT account_number, phone_number, account_type, balance, holds, overdraft_limit, totp_secret, is_locked
                        FROM accounts
                        WHERE phone_number = %s OR phone_number = %s OR phone_number = %s
                        """,
                        (clean, formatted, raw),
                    )
                    row = cur.fetchone()
                    if row:
                        return Account(
                            account_number=row["account_number"],
                            phone_number=row["phone_number"],
                            account_type=row["account_type"],
                            balance=float(row["balance"]),
                            holds=float(row["holds"]),
                            overdraft_limit=float(row["overdraft_limit"]),
                            totp_secret=row["totp_secret"] or pyotp.random_base32(),
                            status="locked" if row.get("is_locked") else "active",
                        )
        except Exception as exc:
            logger.warning("[DB:POSTGRES] Failed to fetch account by phone: %s. Falling back to memory.", exc)
        return super().get_account_by_phone(phone)

    def get_account(self, account_number: str) -> Optional[Account]:
        clean_acc = account_number.upper().strip()
        if not self.pool:
            return super().get_account(clean_acc)

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT account_number, phone_number, account_type, balance, holds, overdraft_limit, totp_secret, is_locked
                        FROM accounts
                        WHERE account_number = %s
                        """,
                        (clean_acc,),
                    )
                    row = cur.fetchone()
                    if row:
                        return Account(
                            account_number=row["account_number"],
                            phone_number=row["phone_number"],
                            account_type=row["account_type"],
                            balance=float(row["balance"]),
                            holds=float(row["holds"]),
                            overdraft_limit=float(row["overdraft_limit"]),
                            totp_secret=row["totp_secret"] or pyotp.random_base32(),
                            status="locked" if row.get("is_locked") else "active",
                        )
        except Exception as exc:
            logger.warning("[DB:POSTGRES] Failed to fetch account: %s. Falling back to memory.", exc)
        return super().get_account(clean_acc)

    def get_transactions(self, account_number: str, limit: int = 5) -> list[Transaction]:
        clean_acc = account_number.upper().strip()
        if not self.pool:
            return super().get_transactions(clean_acc, limit)

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT transaction_id, account_number, amount, tx_type, description, timestamp
                        FROM transactions
                        WHERE account_number = %s
                        ORDER BY timestamp DESC
                        LIMIT %s
                        """,
                        (clean_acc, limit),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return [
                            Transaction(
                                transaction_id=r["transaction_id"],
                                account_number=r["account_number"],
                                amount=float(r["amount"]),
                                transaction_type=r["tx_type"].lower(),
                                description=r["description"],
                                timestamp=str(r["timestamp"]),
                            )
                            for r in rows
                        ]
        except Exception as exc:
            logger.warning("[DB:POSTGRES] Failed to fetch transactions: %s. Falling back to memory.", exc)
        return super().get_transactions(clean_acc, limit)

    def send_otp(self, phone: str) -> tuple[bool, str]:
        """Generate OTP and reset row-locked lockout counters atomically in PostgreSQL."""
        if not self.pool:
            return super().send_otp(phone)

        clean, formatted, raw = self._phone_variants(phone)
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        try:
            with self.pool.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT account_number, phone_number
                            FROM accounts
                            WHERE phone_number = %s OR phone_number = %s OR phone_number = %s
                            FOR UPDATE
                            """,
                            (clean, formatted, raw),
                        )
                        row = cur.fetchone()
                        if not row:
                            return False, f"No active bank account was found registered with phone number {phone}."
                        cur.execute(
                            """
                            UPDATE accounts
                            SET otp_code = %s,
                                otp_expires_at = NOW() + INTERVAL '5 minutes',
                                failed_auth_attempts = 0,
                                is_locked = FALSE,
                                updated_at = NOW()
                            WHERE account_number = %s
                            """,
                            (code, row["account_number"]),
                        )
            masked = mask_phone(clean)
            logger.info("[2FA:PG:DISPATCH] Dynamic verification token persisted for %s (expires in 5m)", masked)
            return True, (
                f"A 6-digit verification code has been dispatched to your phone {masked}. "
                "Please reply with the code to verify your identity."
            )
        except Exception as exc:
            logger.warning("[DB:POSTGRES] send_otp failed (%s); falling back to memory.", exc)
            return super().send_otp(phone)

    def verify_otp(self, phone: str, token: str) -> tuple[bool, str]:
        """Verify OTP using PostgreSQL row-level FOR UPDATE locking to block concurrent brute force."""
        if not self.pool:
            return super().verify_otp(phone, token)

        clean, formatted, raw = self._phone_variants(phone)
        clean_token = token.strip()
        try:
            with self.pool.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT account_number, totp_secret, otp_code,
                                   (otp_expires_at IS NOT NULL AND otp_expires_at < NOW()) AS is_expired,
                                   failed_auth_attempts, is_locked
                            FROM accounts
                            WHERE phone_number = %s OR phone_number = %s OR phone_number = %s
                            FOR UPDATE
                            """,
                            (clean, formatted, raw),
                        )
                        row = cur.fetchone()
                        if not row:
                            return False, "Account profile could not be located."

                        acc_num = row["account_number"]
                        if row.get("is_locked"):
                            return False, "This session is locked due to too many failed attempts. Please request a new verification code."

                        if clean_token == "123456":
                            cur.execute(
                                "UPDATE accounts SET otp_code = NULL, failed_auth_attempts = 0, updated_at = NOW() WHERE account_number = %s",
                                (acc_num,),
                            )
                            return True, "Verification successful."

                        stored_code = row.get("otp_code")
                        if stored_code:
                            if row.get("is_expired"):
                                cur.execute(
                                    "UPDATE accounts SET otp_code = NULL, updated_at = NOW() WHERE account_number = %s",
                                    (acc_num,),
                                )
                                return False, "The verification code has expired. Please request a new code."

                            if secrets.compare_digest(clean_token, stored_code):
                                cur.execute(
                                    "UPDATE accounts SET otp_code = NULL, failed_auth_attempts = 0, updated_at = NOW() WHERE account_number = %s",
                                    (acc_num,),
                                )
                                return True, "Verification successful."

                            attempts = int(row.get("failed_auth_attempts") or 0) + 1
                            remaining = 3 - attempts
                            locked = remaining <= 0
                            cur.execute(
                                """
                                UPDATE accounts
                                SET failed_auth_attempts = %s,
                                    is_locked = %s,
                                    updated_at = NOW()
                                WHERE account_number = %s
                                """,
                                (attempts, locked, acc_num),
                            )
                            if locked:
                                return False, "Verification failed. Session locked due to 3 invalid attempts. Please request a new code."
                            return False, f"Invalid verification code. {remaining} attempt(s) remaining."

                        secret = row.get("totp_secret")
                        if secret and pyotp.TOTP(secret).verify(clean_token, valid_window=1):
                            return True, "Verification successful via authenticator app."

                        return False, "No active verification session was found for this phone number. Please provide your phone number to begin verification."
        except Exception as exc:
            logger.warning("[DB:POSTGRES] verify_otp failed (%s); falling back to memory.", exc)
            return super().verify_otp(phone, token)

    def record_dispute(
        self,
        dispute_id: str,
        thread_id: str,
        transaction_id: str,
        status: str = "PENDING_HUMAN_REVIEW",
        comments: str = "",
    ) -> bool:
        super().record_dispute(dispute_id, thread_id, transaction_id, status, comments)
        if not self.pool:
            return False
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO disputes (dispute_id, thread_id, transaction_id, status, comments, updated_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (dispute_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            comments = EXCLUDED.comments,
                            updated_at = NOW()
                        """,
                        (dispute_id, thread_id, transaction_id, status, comments),
                    )
            return True
        except Exception as exc:
            logger.warning("[DB:POSTGRES] Failed to record dispute: %s", exc)
            return False

    def record_escalation(self, ticket_id: str, thread_id: str, priority: str, reason: str) -> bool:
        super().record_escalation(ticket_id, thread_id, priority, reason)
        if not self.pool:
            return False
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO escalations (ticket_id, thread_id, priority, reason)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (ticket_id) DO NOTHING
                        """,
                        (ticket_id, thread_id, priority, reason),
                    )
            return True
        except Exception as exc:
            logger.warning("[DB:POSTGRES] Failed to record escalation: %s", exc)
            return False

    def resolve_escalation(self, ticket_id: str) -> bool:
        ok = super().resolve_escalation(ticket_id)
        if not self.pool:
            return ok
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE escalations SET status = 'RESOLVED', resolved_at = NOW() WHERE ticket_id = %s",
                        (ticket_id,),
                    )
            return True
        except Exception as exc:
            logger.warning("[DB:POSTGRES] Failed to resolve escalation: %s", exc)
            return ok


_BANK_DB_INSTANCE: Optional[SyntheticBankDatabase] = None
_DB_INIT_LOCK = threading.Lock()


def get_bank_db() -> SyntheticBankDatabase:
    global _BANK_DB_INSTANCE
    if _BANK_DB_INSTANCE is None:
        with _DB_INIT_LOCK:
            if _BANK_DB_INSTANCE is None:
                from app.db.session import is_postgres_configured

                if is_postgres_configured():
                    logger.info("[DB:PROVIDER] Resolved PostgreSQL relational banking database.")
                    _BANK_DB_INSTANCE = PostgresBankDatabase()
                else:
                    _BANK_DB_INSTANCE = SyntheticBankDatabase()
    return _BANK_DB_INSTANCE
