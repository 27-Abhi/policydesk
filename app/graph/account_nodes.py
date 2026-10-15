import logging
import re
from typing import Optional

from app.graph.state import PolicyDeskState
from app.prompts import (
    AUTH_LOCKOUT_TEMPLATE,
    AUTH_OTP_RETRY_TEMPLATE,
    AUTH_PHONE_NOT_FOUND_TEMPLATE,
    AUTH_PHONE_PROMPT,
    AUTH_UNLINKED_ACCOUNT,
    format_account_overview,
)
from app.tools.bank_db import get_bank_db, mask_phone, normalize_phone

logger = logging.getLogger("policydesk.account")


def _extract_phone_number(text: str) -> Optional[str]:
    """Extract a 10-digit phone number from text if present."""
    match = re.search(r"(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})", text)
    if match:
        return "".join(match.groups())
    # Match standalone 10 digits
    digits_only = re.sub(r"\D", "", text)
    if len(digits_only) == 10:
        return digits_only
    if len(digits_only) == 11 and digits_only.startswith("1"):
        return digits_only[1:]
    return None


def _extract_otp_code(text: str) -> Optional[str]:
    """Extract a 6-digit numeric verification code."""
    match = re.search(r"\b\d{6}\b", text.strip())
    if match:
        return match.group(0)
    return None


def handle_account_lookup(state: PolicyDeskState) -> dict:
    """Multi-turn 2FA verified account lookup node."""
    user_input = state.get("user_input", "")
    authenticated = bool(state.get("authenticated", False))
    auth_step = state.get("auth_step")
    auth_phone = state.get("auth_phone")
    account_number = state.get("account_number")
    bank_db = get_bank_db()

    logger.info("[ACCOUNT] Processing lookup. Authenticated=%s, AuthStep=%s, Input=%r", authenticated, auth_step, user_input[:40])

    # 1. User is already authenticated in this session
    if authenticated and account_number:
        acc = bank_db.get_account(account_number)
        if not acc:
            return {
                "answer": "We encountered an issue locating your account records. Please contact Customer Care.",
                "route": "account_lookup",
            }

        input_lower = user_input.lower()
        # Check if user asks specifically for transaction history or statement
        if any(w in input_lower for w in ("transaction", "transactions", "statement", "history", "charges", "recent activity", "spending")):
            txs = bank_db.get_transactions(acc.account_number, limit=5)
            if not txs:
                tx_lines = "No recent transactions found on this account."
            else:
                lines = []
                for t in reversed(txs):
                    sign = "+" if t.transaction_type == "credit" else "-"
                    lines.append(f"  • {t.timestamp}: {t.description} ({sign}${t.amount:,.2f})")
                tx_lines = "\n".join(lines)

            answer = (
                f"Recent transaction activity for your {acc.account_type} ({acc.account_number}):\n"
                f"{tx_lines}\n\n"
                f"Current Balance: ${acc.balance:,.2f} | Available Overdraft: ${acc.overdraft_limit:,.2f}\n"
                f"Is there a specific transaction or dispute you would like assistance with?"
            )
        else:
            # Default overview: balance, holds, overdraft
            answer = (
                f"Here are your account details for {acc.account_type} ({acc.account_number}):\n"
                f"  • Current Balance: ${acc.balance:,.2f}\n"
                f"  • Pending Holds: ${acc.holds:,.2f}\n"
                f"  • Overdraft Protection: ${acc.overdraft_limit:,.2f}\n"
                f"  • Account Status: {acc.status.capitalize()}\n\n"
                f"Would you like me to pull up your recent transaction history or help with anything else?"
            )

        return {
            "answer": answer,
            "route": "account_lookup",
        }

    # 2. User is in the middle of OTP verification
    if auth_step == "awaiting_otp" and auth_phone:
        otp_code = _extract_otp_code(user_input)
        if not otp_code:
            return {
                "answer": f"Please enter the 6-digit verification code sent to your phone {mask_phone(auth_phone)}.",
                "route": "account_lookup",
                "auth_step": "awaiting_otp",
            }

        ok, msg = bank_db.verify_otp(auth_phone, otp_code)
        if ok:
            acc = bank_db.get_account_by_phone(auth_phone)
            if not acc:
                return {
                    "answer": AUTH_UNLINKED_ACCOUNT,
                    "route": "account_lookup",
                    "authenticated": False,
                }

            answer = format_account_overview(
                account_type=acc.account_type,
                account_number=acc.account_number,
                balance=acc.balance,
                holds=acc.holds,
                overdraft_limit=acc.overdraft_limit,
            )
            return {
                "authenticated": True,
                "auth_step": "authenticated",
                "account_number": acc.account_number,
                "answer": answer,
                "route": "account_lookup",
            }
        else:
            # Failed verification
            if "locked" in msg.lower():
                return {
                    "authenticated": False,
                    "auth_step": "awaiting_phone",
                    "answer": AUTH_LOCKOUT_TEMPLATE.format(msg=msg),
                    "route": "account_lookup",
                }
            return {
                "authenticated": False,
                "auth_step": "awaiting_otp",
                "answer": AUTH_OTP_RETRY_TEMPLATE.format(msg=msg),
                "route": "account_lookup",
            }

    # 3. Not authenticated: check for phone number in input or prompt for it
    phone = _extract_phone_number(user_input)
    if phone:
        acc = bank_db.get_account_by_phone(phone)
        if not acc:
            masked = mask_phone(phone)
            return {
                "authenticated": False,
                "auth_step": "awaiting_phone",
                "answer": AUTH_PHONE_NOT_FOUND_TEMPLATE.format(masked=masked),
                "route": "account_lookup",
            }

        ok, msg = bank_db.send_otp(phone)
        return {
            "authenticated": False,
            "auth_step": "awaiting_otp",
            "auth_phone": phone,
            "answer": msg,
            "route": "account_lookup",
        }

    # Prompt user for phone number
    return {
        "authenticated": False,
        "auth_step": "awaiting_phone",
        "answer": AUTH_PHONE_PROMPT,
        "route": "account_lookup",
    }
