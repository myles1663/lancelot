# ==========================================================
# local-llm — llama.cpp inference server for Lancelot
# ==========================================================
# Serves the local utility model via llama-server (HTTP API).
# Model weights are mounted at runtime, never baked into image.
# ==========================================================

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install build dependencies for llama-cpp-python
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r llm && useradd -r -g llm -m -d /home/llm -s /bin/bash llm

WORKDIR /home/llm/app

# Install Python dependencies
COPY requirements-llm.txt .
RUN pip install --no-cache-dir -r requirements-llm.txt

# Copy the local_models package and server code
COPY __init__.py .
COPY lockfile.py .
COPY smoke_test.py .
COPY fetch_model.py .
COPY server.py .
COPY models.lock.yaml .
COPY prompts/ ./prompts/

# Create weights directory (model mounted at runtime)
RUN mkdir -p /home/llm/models && chown -R llm:llm /home/llm

USER llm

# Health check — hits the /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

CMD ["python", "server.py"]
