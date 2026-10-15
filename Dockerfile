# =====================================================================
# PolicyDesk Production Container (AWS EC2 t2.micro / t3.micro Lean Build)
# =====================================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies using pre-compiled wheels (no heavy compiler toolchains)
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application source code and bank policy PDFs
COPY app/ ./app/
COPY data/ ./data/

# Mount point for persistent SQLite WAL checkpoints and uploaded documents
VOLUME ["/app/data"]

# Port 8000: Customer Support Portal (Public)
# Port 8001: Compliance & Operations Portal (Restricted)
EXPOSE 8000 8001

# Run the single-process dual-port asyncio gateway
CMD ["python", "app/serve.py"]
