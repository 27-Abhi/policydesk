import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

load_dotenv()

from app.api.v1.router import api_v1_router

customer_app = FastAPI(
    title="PolicyDesk Customer Support Portal",
    description="Public customer-facing banking AI chat service on Port 8000",
    version="0.1.0",
)

customer_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@customer_app.get("/", response_class=HTMLResponse, tags=["Customer Web App"])
def serve_customer_portal():
    """Serve the customer chat interface."""
    html_file = STATIC_DIR / "customer.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>PolicyDesk Customer Portal</h1><p>static/customer.html not found</p>")


@customer_app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "policydesk-customer-portal", "port": 8000}


# Mount customer API routes (/v1/chat/completions)
customer_app.include_router(api_v1_router, prefix="/v1")
