import asyncio
import logging
import sys
import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("policydesk.server")


from app.db.session import close_pools, init_database


async def run_dual_servers():
    # Initialize relational schema if PostgreSQL is configured
    init_database()

    print("""
======================================================================
🏦  POLICYDESK DUAL-PORT FINANCIAL SERVICES GATEWAY
======================================================================
  🌐 Customer Support Portal (Public):   http://localhost:8000
     • Interactive Banking Chat Interface
     • 2FA Phone & OTP Authentication
     • OpenAI-Compatible Completions (/v1/chat/completions)

  🛡️  Compliance & Operations Center:     http://localhost:8001
     • HITL Dispute Review Board & Approve/Reject Actions
     • Customer Care Priority Ticket Queue
     • RAG & Observability Pipeline Inspector (/v1/chat/debug)
======================================================================
    """)

    config_customer = uvicorn.Config(
        "app.customer_app:customer_app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        access_log=False,
    )
    config_admin = uvicorn.Config(
        "app.admin_app:admin_app",
        host="0.0.0.0",
        port=8001,
        log_level="info",
        access_log=False,
    )

    server_customer = uvicorn.Server(config_customer)
    server_admin = uvicorn.Server(config_admin)

    try:
        await asyncio.gather(server_customer.serve(), server_admin.serve())
    finally:
        await close_pools()


def main():
    try:
        asyncio.run(run_dual_servers())
    except (KeyboardInterrupt, SystemExit):
        logger.info("PolicyDesk dual-port gateway shutdown gracefully.")


if __name__ == "__main__":
    main()

